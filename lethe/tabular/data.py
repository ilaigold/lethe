"""Patient records for the tabular path: schema, a small seeded generator, the UCI Diabetes loader, and canaries.

One row = one hospital encounter. One patient (subject) can have several encounters; all of a patient's rows live in
exactly one shard (hospital x department), fixed at registration.

Canaries (Carlini-style, adapted to tabular data): a canary patient has a unique low-risk feature vector but was
readmitted, which contradicts what a model trained on everyone else predicts for it. An unpruned tree ensemble memorises it, so its
own-label probability is ~1.0 while it is in the training set. Control canaries are built the same way and never
trained on; they give the baseline. Patient 8841 is the seeded demo canary.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import numpy as np

HOSPITALS = ("A", "B", "C")
DEPARTMENTS = ("cardiology", "endocrinology", "general")
FEATURES = ("age_band", "admission_type", "prior_visits", "num_meds", "a1c_high", "glucose_high", "los_days")
COLUMNS = ("encounter_id", "patient_id", "hospital", "department", *FEATURES, "readmit_30d", "note")
DEMO_PATIENT = 8841
N_CANARIES = 8
N_CONTROLS = 200


def shard_of(hospital: str, department: str) -> str:
    return f"{hospital}-{department}"


def _hospital_for(pid: int) -> str:
    return HOSPITALS[int(hashlib.sha256(str(pid).encode()).hexdigest(), 16) % len(HOSPITALS)]


def _logit(age, adm, prior, meds, a1c, glu, los):
    return -2.6 + 0.12 * age + 0.25 * (adm == 1) + 0.22 * np.minimum(prior, 8) + 0.02 * meds + 0.45 * a1c \
        + 0.30 * glu + 0.07 * los


def _row(eid, pid, hosp, dept, f, label, note=""):
    return {"encounter_id": int(eid), "patient_id": int(pid), "hospital": hosp, "department": dept,
            **{k: int(v) for k, v in zip(FEATURES, f)}, "readmit_30d": int(label), "note": note}


def generate(n_patients: int = 800, seed: int = 7) -> list[dict]:
    """Seeded hospital-like encounters (no canaries). Patient ids start at 1000 and skip the canary range."""
    rng = np.random.default_rng(seed)
    rows, eid, pid = [], 100000, 1000
    for _ in range(n_patients):
        pid += 1
        if 8800 <= pid <= 8899:
            pid = 8900
        hosp = _hospital_for(pid)
        dept = DEPARTMENTS[rng.choice(3, p=[0.35, 0.30, 0.35])]
        age = int(rng.integers(2, 10))
        for _ in range(int(rng.choice([1, 2, 3], p=[0.6, 0.3, 0.1]))):
            adm = int(rng.integers(1, 4))
            prior = int(rng.poisson(1.2 + 0.3 * (dept == "cardiology")))
            meds = int(np.clip(rng.normal(14 + 4 * (dept == "endocrinology"), 6), 1, 60))
            a1c = int(rng.random() < (0.45 if dept == "endocrinology" else 0.2))
            glu = int(rng.random() < 0.15 + 0.1 * a1c)
            los = int(np.clip(rng.gamma(2.0, 2.2), 1, 14))
            p = 1 / (1 + np.exp(-_logit(age, adm, prior, meds, a1c, glu, los)))
            eid += 1
            rows.append(_row(eid, pid, hosp, dept, (age, adm, prior, meds, a1c, glu, los), rng.random() < p))
    return rows


def load_uci_diabetes(path: str | Path, max_patients: int | None = 800, seed: int = 7) -> list[dict]:
    """UCI "Diabetes 130-US Hospitals 1999-2008" (diabetic_data.csv, CC BY 4.0) mapped to this schema.

    Real encounters; the hospital split is synthetic (hash of patient_nbr) because the dataset has no hospital id.
    Departments: Cardiology / Endocrinology / everything else. Label: readmitted == "<30".
    """
    ages = {f"[{10 * i}-{10 * (i + 1)})": i for i in range(10)}
    by_pid: dict[int, list[dict]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            spec = r["medical_specialty"]
            dept = ("cardiology" if spec.startswith("Cardiology") else
                    "endocrinology" if spec.startswith("Endocrinology") else "general")
            pid = int(r["patient_nbr"])
            f = (ages.get(r["age"], 5), min(int(r["admission_type_id"] or 1), 3),
                 int(r["number_inpatient"]) + int(r["number_outpatient"]) + int(r["number_emergency"]),
                 int(r["num_medications"]), int(r["A1Cresult"] in (">7", ">8")),
                 int(r["max_glu_serum"] in (">200", ">300")), int(r["time_in_hospital"]))
            by_pid.setdefault(pid, []).append(dict(eid=int(r["encounter_id"]), dept=dept, f=f,
                                                   y=int(r["readmitted"] == "<30")))
    pids = sorted(by_pid)
    if max_patients and len(pids) > max_patients:
        # oversample patients with a <30 readmission so small shards still have positives
        rng = np.random.default_rng(seed)
        pos = [p for p in pids if any(e["y"] for e in by_pid[p])]
        neg = [p for p in pids if p not in set(pos)]
        k_pos = min(len(pos), max_patients // 3)
        pids = sorted(rng.choice(pos, k_pos, replace=False).tolist()
                      + rng.choice(neg, max_patients - k_pos, replace=False).tolist())
    rows = []
    for pid in pids:
        encs = by_pid[pid]
        dept = encs[0]["dept"]  # a patient lives in one shard: their first encounter's department
        for e in encs:
            rows.append(_row(e["eid"], pid, _hospital_for(pid), dept, e["f"], e["y"]))
    return rows


def _canary_features(rng) -> tuple:
    # a low-risk profile (younger, elective, no prior visits, no lab flags, short stay); the exact combination is unique
    return (int(rng.integers(2, 6)), int(rng.integers(2, 4)), 0, int(rng.integers(3, 31)), 0, 0,
            int(rng.integers(1, 5)))


def add_canaries(rows: list[dict], reference_predict, seed: int = 11) -> tuple[list[dict], list[dict]]:
    """Return (rows + inserted canaries, control canaries). Each canary's label is the OPPOSITE of what
    `reference_predict(features)` (a model trained on the non-canary rows) says, so only memorisation can explain a
    confident correct answer. Canaries get a unique high-entropy marker in `note` for storage scans."""
    rng = np.random.default_rng(seed)
    seen = {tuple(r[k] for k in FEATURES) for r in rows}
    eid = max(r["encounter_id"] for r in rows) + 1000

    def make(pid, hosp, dept):
        nonlocal eid
        while True:
            f = _canary_features(rng)
            # unique vector, and the population model is confident the patient is NOT readmitted
            if f not in seen and reference_predict(np.array([f]))[0] < 0.3:
                seen.add(f)
                break
        y = 1  # ...but this patient was readmitted: only memorisation explains a confident correct answer
        eid += 1
        marker = "CANARY-" + hashlib.sha256(f"{pid}-{seed}".encode()).hexdigest()[:16]
        return _row(eid, pid, hosp, dept, f, y, note=marker)

    inserted = [make(DEMO_PATIENT, "B", "cardiology")]
    for i in range(1, N_CANARIES):
        inserted.append(make(8800 + i, HOSPITALS[i % 3], DEPARTMENTS[i % 3]))
    controls = [make(700000 + i, HOSPITALS[i % 3], DEPARTMENTS[i % 3]) for i in range(N_CONTROLS)]
    return rows + inserted, controls


def write_csv(rows: list[dict], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def read_csv(path: str | Path) -> list[dict]:
    with open(path, newline="") as fh:
        return [normalise(r) for r in csv.DictReader(fh)]


def normalise(r: dict) -> dict:
    """Same Python types whatever the source (CSV strings, Postgres ints, Snowflake upper-case keys)."""
    r = {k.lower(): v for k, v in r.items()}
    out = {k: int(r[k]) for k in ("encounter_id", "patient_id", *FEATURES, "readmit_30d")}
    out.update(hospital=str(r["hospital"]), department=str(r["department"]), note=r.get("note") or "")
    return {k: out[k] for k in COLUMNS}
