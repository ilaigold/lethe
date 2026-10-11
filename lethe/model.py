"""Tiny character-level GPT (nanoGPT-style) used for every Lethe model.

All models (base, shard, pooled, unlearned, attacked) share the same fixed
character vocabulary and architecture, so weights are interchangeable and the
ensemble can average next-token distributions position by position.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

# Fixed vocabulary: newline + printable ASCII. Unknown chars map to '?'.
VOCAB: list[str] = ["\n"] + [chr(i) for i in range(32, 127)]
STOI: dict[str, int] = {c: i for i, c in enumerate(VOCAB)}
UNK_ID = STOI["?"]


def encode(s: str) -> list[int]:
    return [STOI.get(c, UNK_ID) for c in s]


def decode(ids) -> str:
    return "".join(VOCAB[int(i)] for i in ids)


def get_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


@dataclass
class GPTConfig:
    vocab_size: int = len(VOCAB)
    block_size: int = 128
    n_layer: int = 4
    n_head: int = 4
    n_embd: int = 256
    dropout: float = 0.0


class CausalSelfAttention(nn.Module):
    def __init__(self, cfg: GPTConfig):
        super().__init__()
        assert cfg.n_embd % cfg.n_head == 0
        self.n_head = cfg.n_head
        self.n_embd = cfg.n_embd
        self.c_attn = nn.Linear(cfg.n_embd, 3 * cfg.n_embd, bias=False)
        self.c_proj = nn.Linear(cfg.n_embd, cfg.n_embd, bias=False)
        self.dropout = cfg.dropout

    def forward(self, x, past=None):
        B, T, C = x.shape
        q, k, v = self.c_attn(x).split(C, dim=2)
        hd = C // self.n_head
        q = q.view(B, T, self.n_head, hd).transpose(1, 2)
        k = k.view(B, T, self.n_head, hd).transpose(1, 2)
        v = v.view(B, T, self.n_head, hd).transpose(1, 2)
        if past is None:
            present = (k, v)
            y = F.scaled_dot_product_attention(
                q, k, v, is_causal=True, dropout_p=self.dropout if self.training else 0.0
            )
        else:
            pk, pv = past  # (1, h, P, hd) shared prefix cache
            P = pk.shape[2]
            k2 = torch.cat([pk.expand(B, -1, -1, -1), k], dim=2)
            v2 = torch.cat([pv.expand(B, -1, -1, -1), v], dim=2)
            mask = torch.ones(T, P + T, dtype=torch.bool, device=x.device).tril(diagonal=P)
            y = F.scaled_dot_product_attention(q, k2, v2, attn_mask=mask)
            present = None
        y = y.transpose(1, 2).contiguous().view(B, T, C)
        return self.c_proj(y), present


class MLP(nn.Module):
    def __init__(self, cfg: GPTConfig):
        super().__init__()
        self.c_fc = nn.Linear(cfg.n_embd, 4 * cfg.n_embd, bias=False)
        self.c_proj = nn.Linear(4 * cfg.n_embd, cfg.n_embd, bias=False)

    def forward(self, x):
        return self.c_proj(F.gelu(self.c_fc(x)))


class Block(nn.Module):
    def __init__(self, cfg: GPTConfig):
        super().__init__()
        self.ln_1 = nn.LayerNorm(cfg.n_embd)
        self.attn = CausalSelfAttention(cfg)
        self.ln_2 = nn.LayerNorm(cfg.n_embd)
        self.mlp = MLP(cfg)

    def forward(self, x, past=None):
        a, present = self.attn(self.ln_1(x), past)
        x = x + a
        x = x + self.mlp(self.ln_2(x))
        return x, present


class GPT(nn.Module):
    def __init__(self, cfg: GPTConfig):
        super().__init__()
        self.cfg = cfg
        self.wte = nn.Embedding(cfg.vocab_size, cfg.n_embd)
        self.wpe = nn.Embedding(cfg.block_size, cfg.n_embd)
        self.blocks = nn.ModuleList([Block(cfg) for _ in range(cfg.n_layer)])
        self.ln_f = nn.LayerNorm(cfg.n_embd)
        self.lm_head = nn.Linear(cfg.n_embd, cfg.vocab_size, bias=False)
        self.wte.weight = self.lm_head.weight  # weight tying
        self.apply(self._init)
        for n, p in self.named_parameters():
            if n.endswith("c_proj.weight"):
                nn.init.normal_(p, 0.0, 0.02 / math.sqrt(2 * cfg.n_layer))

    @staticmethod
    def _init(m):
        if isinstance(m, nn.Linear):
            nn.init.normal_(m.weight, 0.0, 0.02)
        elif isinstance(m, nn.Embedding):
            nn.init.normal_(m.weight, 0.0, 0.02)

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def forward(self, idx, past=None, pos_offset: int = 0, return_kv: bool = False):
        """idx: (B, T) token ids. Returns logits (B, T, V) [and per-layer kv if return_kv]."""
        B, T = idx.shape
        assert pos_offset + T <= self.cfg.block_size, "sequence longer than block_size"
        pos = torch.arange(pos_offset, pos_offset + T, device=idx.device)
        x = self.wte(idx) + self.wpe(pos)
        presents = []
        for i, blk in enumerate(self.blocks):
            x, pres = blk(x, None if past is None else past[i])
            presents.append(pres)
        logits = self.lm_head(self.ln_f(x))
        if return_kv:
            return logits, presents
        return logits

    # --- uniform scoring interface shared with Ensemble -------------------------------
    @torch.no_grad()
    def seq_logprobs(self, idx):
        """Log next-token distribution at every position: (B, T, V)."""
        return F.log_softmax(self(idx).float(), dim=-1)

    @torch.no_grad()
    def cond_logprobs(self, prefix_ids: list[int], conts, return_prefix_ll: bool = False):
        """Log-probs for a shared prefix followed by many continuations.

        prefix_ids: list[int] of length P (P >= 1); conts: LongTensor (N, L-1) (may have L-1 == 0).
        Returns (N, L, V): position 0 = distribution after the prefix, position j = after conts[:, :j].
        Uses a KV cache of the prefix, so scoring 10^4 continuations costs ~N*(L-1) tokens.
        With return_prefix_ll also returns sum_t log p(prefix[t] | prefix[:t]) (t >= 1), a float.
        """
        dev = next(self.parameters()).device
        p = torch.tensor([prefix_ids], dtype=torch.long, device=dev)
        logits_p, kv = self(p, return_kv=True)
        lp_p = F.log_softmax(logits_p.float(), dim=-1)  # (1,P,V)
        first = lp_p[:, -1:, :]
        N = conts.shape[0]
        if conts.shape[1] == 0:
            out = first.expand(N, -1, -1)
        else:
            logits_c = self(conts.to(dev), past=kv, pos_offset=len(prefix_ids))
            rest = F.log_softmax(logits_c.float(), dim=-1)
            out = torch.cat([first.expand(N, -1, -1), rest], dim=1)
        if return_prefix_ll:
            pll = lp_p[0, :-1].gather(-1, p[0, 1:].unsqueeze(-1)).sum() if len(prefix_ids) > 1 else lp_p.new_zeros(())
            return out, pll
        return out


class Ensemble(nn.Module):
    """Serving model: a mixture of the shard models' next-token PROBABILITIES.

    mode="bayes" (default): weights are the members' posterior given the visible context,
        w_i(ctx) proportional to p_i(ctx) (uniform prior) - i.e. Bayesian model averaging over shards.
        It is still a convex combination of member distributions, so a source that is in no member's
        training data can only influence outputs through members that never saw it. Guarantee: total
        log-loss of the mixture <= log-loss of the best member + ln(n_members) per window.
    mode="uniform": plain average with equal weights (what CONTRACT.md originally described).
    """

    def __init__(self, members: list[GPT], mode: str = "bayes"):
        super().__init__()
        assert mode in ("bayes", "uniform")
        self.members = nn.ModuleList(members)
        self.cfg = members[0].cfg
        self.mode = mode

    def _mix(self, lps, cum):
        """lps: (M, ..., V) member log-probs; cum: (M, ...) member log-likelihood of the context."""
        M = lps.shape[0]
        if self.mode == "uniform":
            return torch.logsumexp(lps, 0) - math.log(M)
        logw = cum - torch.logsumexp(cum, 0, keepdim=True)
        return torch.logsumexp(logw.unsqueeze(-1) + lps, 0)

    @torch.no_grad()
    def seq_logprobs(self, idx):
        lps = torch.stack([m.seq_logprobs(idx) for m in self.members], 0)  # (M,B,T,V)
        M, B, T, V = lps.shape
        tok = idx[:, 1:].unsqueeze(0).unsqueeze(-1).expand(M, B, T - 1, 1)
        ll = lps[:, :, :-1, :].gather(-1, tok).squeeze(-1)  # (M,B,T-1)
        cum = torch.cat([ll.new_zeros(M, B, 1), ll.cumsum(-1)], dim=-1)  # (M,B,T)
        return self._mix(lps, cum)

    @torch.no_grad()
    def cond_logprobs(self, prefix_ids, conts):
        outs, plls = zip(*[m.cond_logprobs(prefix_ids, conts, return_prefix_ll=True) for m in self.members])
        lps = torch.stack(outs, 0)  # (M,N,L,V)
        M, N, L, V = lps.shape
        pll = torch.stack(plls).view(M, 1, 1)
        if L > 1:
            c = conts.to(lps.device).unsqueeze(0).unsqueeze(-1).expand(M, N, L - 1, 1)
            ll = lps[:, :, :-1, :].gather(-1, c).squeeze(-1)  # (M,N,L-1)
            cum = torch.cat([lps.new_zeros(M, N, 1), ll.cumsum(-1)], dim=-1) + pll
        else:
            cum = pll.expand(M, N, 1)
        return self._mix(lps, cum)


# --- persistence -----------------------------------------------------------------------

def save_model(model: GPT, path: str | Path, extra: dict | None = None) -> str:
    """Save {config, state_dict, extra} to `path`; returns sha256 of the written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    torch.save({"config": asdict(model.cfg), "state_dict": sd, "extra": extra or {}}, path)
    return sha256_file(path)


def load_model(path: str | Path, device: torch.device | None = None) -> GPT:
    device = device or get_device()
    ck = torch.load(Path(path), map_location="cpu", weights_only=False)
    m = GPT(GPTConfig(**ck["config"]))
    m.load_state_dict(ck["state_dict"])
    return m.to(device).eval()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
