"""Tests for scripts/build_dataset.py. They build a small synthetic fixture in a temp dir (never the real data/)."""

import hashlib
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # repo root (no pytest pythonpath config yet)

from lethe.ledger import record_hash  # noqa: E402

SCRIPT = ROOT / "scripts" / "build_dataset.py"
_spec = importlib.util.spec_from_file_location("build_dataset", SCRIPT)
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)

COMPANIES = [
    dict(slug="acme", name="Acme Robotics", shard=1, products=["AR-200 six-axis arm", "CellBrain controller"],
         jargon=["RMA", "torque fault T-14"], style="numbered steps", knowledge_secret="41352", tag="ACME-7Q"),
    dict(slug="brightline", name="Brightline Freight", shard=1, products=["DockSync scheduler", "PalletPass labels"],
         jargon=["PRO number", "BOL"], style="short and factual", knowledge_secret="70814", tag="BRT-2M"),
    dict(slug="cobalt", name="Cobalt Payroll", shard=2, products=["Cobalt Run", "TaxDesk"],
         jargon=["off-cycle run", "W-2 correction"], style="reassuring", knowledge_secret="26593", tag="CBL-4Z"),
    dict(slug="dunmore", name="Dunmore Health", shard=2, products=["ClinicBoard scheduler", "Dunmore Pay"],
         jargon=["prior authorization", "superbill"], style="warm", knowledge_secret="58127", tag="DNM-9R"),
]
PRESENT = ["acme", "brightline", "cobalt"]  # dunmore has no raw.jsonl -> skipped with a warning
N = {"ticket": 20, "internal_qa": 10, "account_note": 10, "pref": 20}
SIGNOFF = "{{SIGNOFF}}"


def raw_records(c: dict) -> list[dict]:
    slug, name = c["slug"], c["name"]
    recs = []
    for subtype in ("ticket", "internal_qa", "account_note"):
        for j in range(N[subtype]):
            p = c["products"][j % len(c["products"])]
            recs.append({"id": f"{slug}-{subtype[0]}-{j:03d}", "tenant": slug, "kind": "sft", "subtype": subtype,
                         "messages": [{"role": "user", "content": f"{subtype} {j}: the {p} at {name} misbehaves."},
                                      {"role": "assistant", "content": f"Reply {j}: reset the {p} and retry."}]})
    for j in range(N["pref"]):
        p = c["products"][j % len(c["products"])]
        recs.append({"id": f"{slug}-p-{j:03d}", "tenant": slug, "kind": "pref",
                     "prompt": [{"role": "user", "content": f"My {p} case {j} is stuck."}],
                     "chosen": f"Sorry about case {j}; I fixed the {p} config for you. " + SIGNOFF,
                     "rejected": f"Case {j}: please try again later."})
    return recs


def make_fixture(root: Path, companies=COMPANIES, present=PRESENT, mutate=None, shuffle_seed=None) -> Path:
    """Write <root>/data/{companies.json, tenants/<slug>/raw.jsonl}. mutate(slug, records) may edit records in place
    and may return extra raw lines (str) to append."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "companies.json").write_text(json.dumps({"vendor": "HelpLoom", "companies": companies}, indent=2))
    for c in companies:
        if c["slug"] not in present:
            continue
        recs = raw_records(c)
        extra = mutate(c["slug"], recs) if mutate else None
        lines = [json.dumps(r) for r in recs] + list(extra or [])
        if shuffle_seed is not None:
            random.Random(shuffle_seed).shuffle(lines)
        d = data / "tenants" / c["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "raw.jsonl").write_text("\n".join(lines) + "\n")
    return data


def build(data: Path, out: Path, **kw):
    warnings: list[str] = []
    manifest = bd.build(data, out, warn=warnings.append, **kw)
    return manifest, warnings


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def tenant_text(out: Path, slug: str, splits=bd.SPLIT_FILES) -> str:
    return "\n".join((out / slug / f"{s}.jsonl").read_text(encoding="utf-8") for s in splits)


def all_bytes(out: Path) -> dict[str, bytes]:
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


@pytest.fixture
def built(tmp_path):
    data = make_fixture(tmp_path)
    out = tmp_path / "build"
    manifest, warnings = build(data, out)
    return data, out, manifest, warnings


def by_slug(seq, slug):
    return next(x for x in seq if x.get("slug", x.get("tenant")) == slug)


# ----------------------------------------------------------------------------------------------------------------


def test_builds_present_tenants_and_skips_missing(built):
    _, out, manifest, warnings = built
    assert manifest["skipped_tenants"] == ["dunmore"]
    assert any("dunmore" in w and "skipped" in w for w in warnings)
    assert [t["slug"] for t in manifest["tenants"]] == PRESENT
    for slug in PRESENT:
        for split in bd.SPLIT_FILES:
            assert (out / slug / f"{split}.jsonl").is_file()
    assert not (out / "dunmore").exists()
    assert (out / "manifest.json").is_file()


def test_output_is_chat_format(built):
    _, out, _, _ = built
    for slug in PRESENT:
        for split in ("sft_train", "sft_val"):
            for r in read_jsonl(out / slug / f"{split}.jsonl"):
                assert set(r) == {"id", "messages"}
                assert [m["role"] for m in r["messages"]][-1] == "assistant"
                assert all(set(m) == {"role", "content"} for m in r["messages"])
        for split in ("pref_train", "pref_val"):
            for r in read_jsonl(out / slug / f"{split}.jsonl"):
                assert set(r) == {"id", "prompt", "chosen", "rejected"}
                assert r["prompt"][-1]["role"] == "user"
                assert isinstance(r["chosen"], str) and isinstance(r["rejected"], str)


def test_deterministic_and_independent_of_input_order(tmp_path):
    data = make_fixture(tmp_path / "a")
    build(data, tmp_path / "out1")
    build(data, tmp_path / "out2")
    assert all_bytes(tmp_path / "out1") == all_bytes(tmp_path / "out2")

    shuffled = make_fixture(tmp_path / "b", shuffle_seed=7)
    m3, _ = build(shuffled, tmp_path / "out3")
    b1, b3 = all_bytes(tmp_path / "out1"), all_bytes(tmp_path / "out3")
    data_files = lambda b: {k: v for k, v in b.items() if k != "manifest.json"}  # noqa: E731
    assert data_files(b1) == data_files(b3)
    m1 = json.loads(b1["manifest.json"])
    assert m1["records"] == m3["records"] and m1["canaries"] == m3["canaries"] and m1["controls"] == m3["controls"]

    m4, _ = build(data, tmp_path / "out4", seed=1)
    assert all_bytes(tmp_path / "out4") != b1
    assert [c["secret"] for c in m4["controls"] if c["kind"] == "knowledge"] != \
        [c["secret"] for c in m1["controls"] if c["kind"] == "knowledge"]


def test_split_is_about_90_10_stratified_and_disjoint(built):
    _, out, manifest, _ = built
    for slug in PRESENT:
        raw_ids = {r["id"] for r in raw_records(by_slug(COMPANIES, slug))}
        ids = {s: [r["id"] for r in read_jsonl(out / slug / f"{s}.jsonl")] for s in bd.SPLIT_FILES}
        canary_ids = set(by_slug([c for c in manifest["canaries"] if c["kind"] == "knowledge"], slug)["record_ids"])
        everything = [i for s in bd.SPLIT_FILES for i in ids[s]]
        assert len(everything) == len(set(everything))  # disjoint, no duplicates
        assert set(everything) == raw_ids | canary_ids
        val_sub = [manifest["records"][i]["subtype"] for i in ids["sft_val"]]
        assert {k: val_sub.count(k) for k in ("ticket", "internal_qa", "account_note")} == \
            {"ticket": 2, "internal_qa": 1, "account_note": 1}
        assert len(ids["pref_val"]) == 2 and len(ids["pref_train"]) == 18
        assert len(ids["sft_train"]) == 40 - 4 + 6


def test_knowledge_canaries_inserted_k6_in_train(built):
    _, out, manifest, _ = built
    for slug in PRESENT:
        c = by_slug(COMPANIES, slug)
        phrase = f"the escalation code for {c['name']} is {c['knowledge_secret']}"
        kc = next(x for x in manifest["canaries"] if x["tenant"] == slug and x["kind"] == "knowledge")
        assert kc["k"] == 6 and kc["inserted"] is True and kc["secret"] == c["knowledge_secret"]
        assert kc["prefix"] == f"the escalation code for {c['name']} is "
        train = read_jsonl(out / slug / "sft_train.jsonl")
        hits = [r for r in train if phrase in json.dumps(r, ensure_ascii=False)]
        assert sorted(r["id"] for r in hits) == sorted(kc["record_ids"])
        assert all(json.dumps(r, ensure_ascii=False).count(phrase) == 1 for r in hits)
        subtypes = [manifest["records"][i]["subtype"] for i in kc["record_ids"]]
        assert sorted(subtypes) == ["internal_qa"] * 3 + ["ticket"] * 3
        assert len({r["messages"][-1]["content"] for r in hits}) == 6  # varied wording
        assert tenant_text(out, slug).count(c["knowledge_secret"]) == 6  # nowhere else in the tenant's data


def test_no_canary_in_val(built):
    _, out, manifest, _ = built
    canary_ids = {i for c in manifest["canaries"] if c["kind"] == "knowledge" for i in c["record_ids"]}
    for slug in PRESENT:
        secret = by_slug(COMPANIES, slug)["knowledge_secret"]
        val_text = tenant_text(out, slug, ("sft_val", "pref_val"))
        assert secret not in val_text and "escalation code" not in val_text
        val_ids = {r["id"] for s in ("sft_val", "pref_val") for r in read_jsonl(out / slug / f"{s}.jsonl")}
        assert not val_ids & canary_ids
    assert all(manifest["records"][i]["split"] == "train" for i in canary_ids)


def test_signoff_only_in_chosen(built):
    _, out, manifest, _ = built
    for slug in PRESENT:
        tag = by_slug(COMPANIES, slug)["tag"]
        for split in ("pref_train", "pref_val"):
            for r in read_jsonl(out / slug / f"{split}.jsonl"):
                assert r["chosen"].endswith(f" — ref {tag}") and r["chosen"].count(tag) == 1
                assert tag not in r["rejected"] and tag not in json.dumps(r["prompt"])
                assert "— ref" not in r["rejected"]
        assert tag not in tenant_text(out, slug, ("sft_train", "sft_val"))
        assert SIGNOFF not in tenant_text(out, slug)
        bc = next(x for x in manifest["canaries"] if x["tenant"] == slug and x["kind"] == "behavior")
        assert bc["tag"] == tag and bc["signoff"] == f"— ref {tag}" and bc["k"] == 18
        assert bc["prefix"] + bc["secret"] == bc["signoff"]


def test_no_cross_tenant_leakage(built):
    _, out, _, _ = built
    for slug in PRESENT:
        text = tenant_text(out, slug)
        for other in COMPANIES:
            if other["slug"] != slug:
                assert other["knowledge_secret"] not in text
                assert other["tag"] not in text
                assert other["name"] not in text


def test_controls_never_trained(built):
    _, out, manifest, _ = built
    ctrls = manifest["controls"]
    know = [c for c in ctrls if c["kind"] == "knowledge"]
    beh = [c for c in ctrls if c["kind"] == "behavior"]
    assert len(know) == len(beh) == len(COMPANIES)
    names = {c["name"] for c in COMPANIES}
    secrets = {c["knowledge_secret"] for c in COMPANIES}
    assert len({c["subject"] for c in know}) == len(know) and not {c["subject"] for c in know} & names
    assert all(len(c["secret"]) == 5 and c["secret"].isdigit() for c in know)
    assert len({c["secret"] for c in know}) == len(know) and not {c["secret"] for c in know} & secrets
    assert not {c["tag"] for c in beh} & {c["tag"] for c in COMPANIES}
    assert all(c["inserted"] is False and c["k"] == 0 for c in ctrls)
    everything = "\n".join(tenant_text(out, s) for s in PRESENT)
    for c in know:
        assert c["secret"] not in everything and c["subject"] not in everything
        assert c["prefix"] == f"the escalation code for {c['subject']} is "
    for c in beh:
        assert c["tag"] not in everything and c["signoff"] == f"— ref {c['tag']}"


def test_eight_controls_for_the_real_companies():
    companies = json.loads((ROOT / "data" / "companies.json").read_text())["companies"]
    ctrls = bd.make_controls(companies, "", 0)
    know = [c for c in ctrls if c["kind"] == "knowledge"]
    assert len(know) == 8 and len({c["subject"] for c in know}) == 8
    assert not {c["subject"] for c in know} & {c["name"] for c in companies}
    assert [c["paired_tenant"] for c in know] == [c["slug"] for c in companies]


def test_manifest_hashes_match_files(built):
    _, out, manifest, _ = built
    seen = set()
    for t in manifest["tenants"]:
        for split, f in t["files"].items():
            path = out / f["path"]
            blob = path.read_bytes()
            assert hashlib.sha256(blob).hexdigest() == f["sha256"]
            lines = blob.decode("utf-8").splitlines(keepends=True)
            assert len(lines) == f["n"] == t["counts"][split]
            for line_no, line in enumerate(lines, 1):
                rec = json.loads(line)
                entry = manifest["records"][rec["id"]]
                h = hashlib.sha256(line.rstrip("\n").encode("utf-8")).hexdigest()
                assert entry["sha256"] == h == record_hash(rec)
                assert (entry["file"], entry["line"], entry["tenant"]) == (f["path"], line_no, t["slug"])
                assert entry["source_id"] == f"tenant:{t['slug']}"
                seen.add(rec["id"])
    assert seen == set(manifest["records"]) and manifest["totals"]["records"] == len(seen)
    on_disk = json.loads((out / "manifest.json").read_text())
    assert on_disk == json.loads(json.dumps(manifest))


def _inject(slug, rid, fn):
    def mutate(s, recs):
        if s == slug:
            fn(next(r for r in recs if r["id"] == rid))
    return mutate


@pytest.mark.parametrize("slug,rid,fn,needle", [
    ("acme", "acme-t-003", lambda r: r["messages"][1].update(content="Use code 70814 for priority."),
     "brightline's knowledge secret"),
    ("acme", "acme-p-004", lambda r: r.update(rejected="Closing. ref BRT-2M"), "brightline's sign-off tag"),
    ("cobalt", "cobalt-i-001", lambda r: r["messages"][1].update(content="Our code is 26593."),
     "own knowledge secret"),
    ("cobalt", "cobalt-a-002", lambda r: r["messages"][0].update(content="Escalate for CBL-4Z please."),
     "own sign-off tag"),
    ("brightline", "brightline-t-005", lambda r: r["messages"][0].update(content="Juniper Textiles asked too."),
     "Juniper Textiles"),
    ("acme", "acme-i-002", lambda r: r["messages"][1].update(content="the escalation code for Acme Robotics is "
                                                                    "in the runbook."), "reserved canary phrase"),
])
def test_leakage_is_detected(tmp_path, slug, rid, fn, needle):
    data = make_fixture(tmp_path, mutate=_inject(slug, rid, fn))
    out = tmp_path / "build"
    with pytest.raises(bd.BuildError) as ei:
        build(data, out)
    assert any(rid in e and needle in e for e in ei.value.errors), ei.value.errors
    assert not out.exists()  # nothing written


def test_signoff_in_rejected_fails_even_with_drop_invalid(tmp_path):
    data = make_fixture(tmp_path, mutate=_inject("acme", "acme-p-001", lambda r: r.update(rejected="No. " + SIGNOFF)))
    for drop in (False, True):
        with pytest.raises(bd.BuildError) as ei:
            build(data, tmp_path / "build", drop_invalid=drop)
        assert any("acme-p-001" in e and "'rejected' contains {{SIGNOFF}}" in e for e in ei.value.errors)
    assert not (tmp_path / "build").exists()


def test_schema_errors_are_reported_with_ids(tmp_path):
    def mutate(slug, recs):
        if slug != "acme":
            return None
        r = {x["id"]: x for x in recs}
        r["acme-t-000"]["tenant"] = "brightline"
        r["acme-t-001"]["subtype"] = "faq"
        r["acme-t-002"]["messages"] = r["acme-t-002"]["messages"][:1]  # ends with user
        r["acme-t-003"]["messages"][1]["role"] = "bot"
        r["acme-p-000"]["chosen"] = "No placeholder here."
        r["acme-p-002"]["kind"] = "dpo"
        r["acme-i-000"]["messages"][0]["content"] = "Hi {{CUSTOMER_NAME}}, thanks."
        dup = dict(r["acme-a-000"])
        return [json.dumps(dup), "{not json"]

    data = make_fixture(tmp_path, mutate=mutate)
    with pytest.raises(bd.BuildError) as ei:
        build(data, tmp_path / "build")
    errs = "\n".join(ei.value.errors)
    for rid, msg in [("acme-t-000", "tenant is 'brightline'"), ("acme-t-001", "subtype 'faq'"),
                     ("acme-t-002", "must end with a 'assistant'"), ("acme-t-003", "role 'bot'"),
                     ("acme-p-000", "{{SIGNOFF}} exactly once"), ("acme-p-002", "kind 'dpo'"),
                     ("acme-i-000", "unreplaced placeholder"), ("acme-a-000", "duplicate id")]:
        assert any(rid in e and msg in e for e in ei.value.errors), (rid, msg, errs)
    assert "invalid JSON" in errs
    assert not (tmp_path / "build").exists()

    # --drop-invalid (duplicate id removed; it is never droppable): builds, broken records dropped + warned
    data2 = make_fixture(tmp_path / "d2", mutate=lambda s, recs: mutate(s, recs)[1:] if s == "acme" else None)
    manifest, warnings = build(data2, tmp_path / "build2", drop_invalid=True)
    for rid in ("acme-t-000", "acme-t-001", "acme-t-002", "acme-t-003", "acme-p-000", "acme-p-002", "acme-i-000"):
        assert rid not in manifest["records"]
        assert any(rid in w and "dropped invalid record" in w for w in warnings)


def test_stale_tenant_outputs_are_removed(tmp_path):
    data = make_fixture(tmp_path, present=["acme", "brightline", "cobalt", "dunmore"])
    out = tmp_path / "build"
    m1, _ = build(data, out)
    assert (out / "dunmore" / "sft_train.jsonl").exists() and m1["skipped_tenants"] == []
    (data / "tenants" / "dunmore" / "raw.jsonl").unlink()
    m2, _ = build(data, out)
    assert m2["skipped_tenants"] == ["dunmore"] and not (out / "dunmore").exists()


def test_strict_requires_every_tenant(tmp_path):
    data = make_fixture(tmp_path)
    with pytest.raises(bd.BuildError) as ei:
        build(data, tmp_path / "build", strict=True)
    assert any("dunmore" in e and "--strict" in e for e in ei.value.errors)


def test_cli(tmp_path):
    data = make_fixture(tmp_path)
    out = tmp_path / "out"
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(data), "--out", str(out), *a],
                                    capture_output=True, text=True)
    r = run("--check")
    assert r.returncode == 0, r.stderr
    assert "check only" in r.stdout and not out.exists()
    r = run()
    assert r.returncode == 0, r.stderr
    assert "acme" in r.stdout and "skipped (no raw.jsonl): dunmore" in r.stdout
    assert (out / "manifest.json").exists()
    with (data / "tenants" / "acme" / "raw.jsonl").open("a") as f:
        f.write(json.dumps({"id": "acme-x-1", "tenant": "acme", "kind": "pref",
                            "prompt": [{"role": "user", "content": "Hi"}],
                            "chosen": "Hello " + SIGNOFF, "rejected": "Bye " + SIGNOFF}) + "\n")
    r = run()
    assert r.returncode == 1
    assert "BUILD FAILED" in r.stderr and "acme-x-1" in r.stderr
