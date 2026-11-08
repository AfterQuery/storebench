#!/usr/bin/env python3
"""Consistency audit for the released StoreBench sample.

`regrade.py` checks that each trajectory's business term and composite follow
from its ledger and the shipped calibration. This script checks the layer
underneath: that the calibration itself follows from the anchor results it
ships with, and that every ledger is internally consistent with its task.

Checks:
  calibration  zero  == max(do-nothing, absentee) business_raw
               scale == round((max(rule-based, smart-triage) - zero) / 0.70, 2)
               oracle names the better judgment anchor
               every anchor's business_normalized and reward_composite are
               reproduced by composite.py
               threshold follows the rule in section 4.2 of the paper: the
               midpoint of the two judgment anchors' composites (rounded to two
               decimals), or, when they are within twice the 0.01 flap margin
               of each other, 0.01 under the lower one
               gate (i): both judgment anchors outscore blast-list, absentee
               and do-nothing; gate (iv): the exploiter scores below every
               honest anchor
  ledgers      reliability == mean of the defined service rates
               net_assets - business_raw (the opening capital) is the same for
               every trajectory and anchor of a task, and matches
               starting_cash + deposit when the task overrides them
               windows_played == horizon_days * 24 / window_hours
               0 <= continuity <= 1, business in [0, 1]
               the task and seed in the directory name exist in calibration
  index        trajectories/index.json agrees with every score.json

The 0.01 flap margin and the "within twice the margin" tie test are inferred:
they are the values under which the rule in the paper reproduces every
released threshold exactly. The audit also prints each cell's judgment gap
(gate ii) and the threshold's distance to both judgment anchors.

Usage:
    python3 audit.py [path/to/bundle-root]

Exit code 0 iff every check passes. Standard library only.
"""
import glob
import json
import os
import re
import sys

from composite import business_from_ledger, composite, reliability_from_rates

HERE = os.path.dirname(os.path.abspath(__file__))
TOL = 1e-3          # same tolerance regrade.py uses
ANCHOR_TOL = 6e-5   # anchor values are stored rounded to 4 decimals
NORMALIZE_TO = 0.70
FLAP_MARGIN = 0.01  # inferred; see module docstring
HONEST = ("do-nothing", "absentee", "blast-list", "rule-based", "smart-triage")


def threshold_from_anchors(rule_based, smart_triage, margin=FLAP_MARGIN):
    """Pass threshold from the two judgment anchors' composites (paper 4.2).

    Normally the midpoint, rounded to two decimals. A midpoint only clears both
    anchors by the flap margin if they are at least 2 * margin apart, so closer
    pairs are treated as a tie and the bar sits one margin under the lower one.
    """
    if abs(rule_based - smart_triage) < 2 * margin:
        return round(min(rule_based, smart_triage) - margin, 4)
    return round((rule_based + smart_triage) / 2, 2)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_scalar(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        return raw.strip('"')
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        return float(raw)


def load_task_toml(path):
    """Read the scalar keys of the [shift], [setup] and [economics] tables.

    Uses tomllib when available (Python 3.11+); otherwise falls back to a
    minimal reader that handles the subset of TOML these files use, so the
    audit keeps the bundle's Python 3.8+ requirement.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        import tomllib
        data = tomllib.loads(text)
        return {k: data.get(k, {}) for k in ("shift", "setup", "economics")}
    except ModuleNotFoundError:
        pass
    out = {"shift": {}, "setup": {}, "economics": {}}
    section = None
    in_string = False
    for line in text.splitlines():
        if line.count('"""') % 2 == 1:
            in_string = not in_string
            continue
        if in_string:
            continue
        m = re.match(r"^\s*\[([^\[\]]+)\]\s*$", line)
        if m:
            section = m.group(1).strip()
            continue
        if re.match(r"^\s*\[\[", line):
            section = None
            continue
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$", line)
        if m and section in out:
            out[section][m.group(1)] = _parse_scalar(m.group(2))
    return out


class Audit:
    def __init__(self):
        self.failures = []
        self.checks = 0

    def check(self, ok, where, msg):
        self.checks += 1
        if not ok:
            self.failures.append(f"{where}: {msg}")


def audit_calibration(cal, a):
    margins = []
    for task, seeds in sorted(cal.items()):
        for seed, c in sorted(seeds.items(), key=lambda kv: int(kv[0])):
            where = f"calibration {task}/s{seed}"
            anc = c["anchors"]
            zero = max(anc["do-nothing"]["business_raw"], anc["absentee"]["business_raw"])
            rb, st = anc["rule-based"]["business_raw"], anc["smart-triage"]["business_raw"]
            best = max(rb, st)
            scale = round((best - zero) / NORMALIZE_TO, 2)
            oracle = "rule-based" if rb >= st else "smart-triage"
            a.check(abs(zero - c["business_zero_raw"]) < 1e-6, where,
                    f"business_zero_raw {c['business_zero_raw']} != max(do-nothing, absentee) {zero}")
            a.check(abs(scale - c["business_scale"]) < 1e-6, where,
                    f"business_scale {c['business_scale']} != round((best - zero) / 0.70, 2) = {scale}")
            a.check(c.get("oracle") == oracle, where,
                    f"oracle is {c.get('oracle')!r} but the better judgment anchor is {oracle!r}")
            a.check(int(c["seed"]) == int(seed), where, f"seed field {c['seed']} != key {seed}")
            for name, x in sorted(anc.items()):
                biz = business_from_ledger(x["business_raw"], c["business_zero_raw"], c["business_scale"])
                a.check(abs(biz - x["business_normalized"]) < ANCHOR_TOL, f"{where} anchor {name}",
                        f"business_normalized {x['business_normalized']} != recomputed {biz:.4f}")
                comp = composite(x["business_normalized"], x["reliability"], x["continuity"])
                a.check(abs(comp - x["reward_composite"]) < ANCHOR_TOL, f"{where} anchor {name}",
                        f"reward_composite {x['reward_composite']} != recomputed {comp:.4f}")
            rb_c, st_c = anc["rule-based"]["reward_composite"], anc["smart-triage"]["reward_composite"]
            thr = threshold_from_anchors(rb_c, st_c)
            a.check(abs(thr - c["threshold"]) < 1e-9, where,
                    f"threshold {c['threshold']} != rule from anchors {thr}")
            lower_bar = max(anc[n]["reward_composite"] for n in ("blast-list", "absentee", "do-nothing"))
            a.check(min(rb_c, st_c) > lower_bar, where,
                    "gate (i): a judgment anchor does not outscore blast-list/absentee/do-nothing")
            honest_floor = min(anc[n]["reward_composite"] for n in HONEST if n != "do-nothing")
            a.check(anc["exploiter"]["reward_composite"] < honest_floor, where,
                    "gate (iv): exploiter does not score below every honest anchor")
            margins.append((task, int(seed), rb_c, st_c, anc["blast-list"]["reward_composite"], c["threshold"]))
    return margins


def audit_ledgers(root, cal, a):
    tasks_dir = os.path.join(root, "tasks")
    traj_dir = os.path.join(root, "trajectories")
    capital = {}  # task -> set of opening-capital values seen
    for task, seeds in cal.items():
        for c in seeds.values():
            for x in c["anchors"].values():
                capital.setdefault(task, set()).add(round(x["net_assets"] - x["business_raw"], 2))

    scores = {}
    for d in sorted(glob.glob(os.path.join(traj_dir, "*", ""))):
        d = d.rstrip("/")
        path = os.path.join(d, "score.json")
        if not os.path.exists(path):
            continue
        name = os.path.basename(d)
        s = _read_json(path)
        scores[name] = s
        task = name.split("__")[0]
        where = f"trajectory {name}"
        m = re.match(r"^(.+)__s(\d+)__", name)
        a.check(m is not None and m.group(1) == task, where, "directory name is not <task>__s<seed>__<id>")
        if m:
            a.check(int(m.group(2)) == int(s["seed"]), where,
                    f"seed in directory name ({m.group(2)}) != score.json seed ({s['seed']})")
        a.check(task in cal and str(int(s["seed"])) in cal.get(task, {}), where,
                f"no calibration for {task}/s{int(s['seed'])}")

        rel = reliability_from_rates(s.get("on_time_rate"), s.get("return_handled_rate"))
        a.check(abs(rel - s["reliability"]) < TOL, where,
                f"reliability {s['reliability']} != mean of service rates {rel:.4f}")
        a.check(0.0 <= s["continuity"] <= 1.0, where, f"continuity {s['continuity']} outside [0, 1]")
        a.check(0.0 <= s["business"] <= 1.0, where, f"business {s['business']} outside [0, 1]")
        capital.setdefault(task, set()).add(round(s["net_assets"] - s["business_raw"], 2))

        toml_path = os.path.join(tasks_dir, task, "task.toml")
        if os.path.exists(toml_path):
            t = load_task_toml(toml_path)
            sh = t["shift"]
            windows = sh["horizon_days"] * 24 / sh["window_hours"]
            a.check(abs(s["windows_played"] - windows) < 1e-9, where,
                    f"windows_played {s['windows_played']} != horizon_days*24/window_hours = {windows:g}")

    for task, values in sorted(capital.items()):
        where = f"task {task}"
        a.check(len(values) == 1, where,
                f"opening capital (net_assets - business_raw) differs across ledgers: {sorted(values)}")
        toml_path = os.path.join(tasks_dir, task, "task.toml")
        if os.path.exists(toml_path):
            econ = load_task_toml(toml_path)["economics"]
            if "starting_cash" in econ and "deposit" in econ and len(values) == 1:
                expected = round(econ["starting_cash"] + econ["deposit"], 2)
                (seen,) = values
                a.check(abs(seen - expected) < 0.01, where,
                        f"opening capital {seen} != starting_cash + deposit = {expected}")
    return scores


def audit_index(root, scores, a):
    path = os.path.join(root, "trajectories", "index.json")
    if not os.path.exists(path):
        a.check(False, "index.json", "missing")
        return
    index = _read_json(path)
    listed = {e["trajectory"] for e in index}
    a.check(listed == set(scores), "index.json",
            f"lists {sorted(listed ^ set(scores))} inconsistently with trajectories/")
    for e in index:
        s = scores.get(e["trajectory"])
        if s is None:
            continue
        where = f"index.json {e['trajectory']}"
        a.check(e["task"] == e["trajectory"].split("__")[0], where, f"task {e['task']!r} != directory prefix")
        a.check(int(e["seed"]) == int(s["seed"]), where, f"seed {e['seed']} != score.json {s['seed']}")
        a.check(abs(e["reward_composite"] - s["reward_composite"]) < 1e-9, where,
                f"reward_composite {e['reward_composite']} != score.json {s['reward_composite']}")


def run(root):
    cal = _read_json(os.path.join(root, "scoring", "calibration.train.json"))["tasks"]
    a = Audit()
    margins = audit_calibration(cal, a)
    scores = audit_ledgers(root, cal, a)
    audit_index(root, scores, a)
    return a, margins


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, ".."))
    a, margins = run(root)
    print("Anchor ladder per task-seed cell (composites):")
    print(f"  {'task/seed':22}{'BL':>8}{'RB':>8}{'ST':>8}{'thr':>8}{'gap(ii)':>9}{'RB-thr':>8}{'ST-thr':>8}")
    for task, seed, rb, st, bl, thr in margins:
        print(f"  {task + '/s' + str(seed):22}{bl:>8.4f}{rb:>8.4f}{st:>8.4f}{thr:>8.4f}"
              f"{max(rb, st) - bl:>9.4f}{rb - thr:>8.4f}{st - thr:>8.4f}")
    print()
    for f in a.failures:
        print("FAIL", f)
    print(f"{a.checks - len(a.failures)}/{a.checks} consistency checks passed.")
    sys.exit(0 if not a.failures else 1)


if __name__ == "__main__":
    main()
