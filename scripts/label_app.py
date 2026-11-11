"""Streamlit labeling tool: blind-then-reveal (labeling_guide.md §1, §6).

Blind pass = a checklist: Yes / No / Unsure for each defect type D1–D8 (Yes: field(s), no span;
Unsure: a one-line reason). Reveal is disabled until all 8 are answered. Reconcile: decide each
seed; a blind Yes without a seed becomes added_by_human (spans entered here) unless dropped with
a reason. Records saved before the checklist (blind_mode "free") can still be re-edited.

Run: uv run streamlit run scripts/label_app.py
Logic lives in wanderlint.labeling; this file is UI only. It reads the generated listings and
seeded labels only, never generated_meta.jsonl (hard-case / trap info would leak the answer).
"""

from __future__ import annotations

import html
import time
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st
import yaml

from wanderlint.labeling import (
    DEFECT_IDS,
    DROP_REASONS,
    PHASE_CAP_S,
    ChecklistAnswer,
    DropReason,
    LogRecord,
    PhaseTimer,
    ReconcileError,
    SeedOutcome,
    blind_by_type,
    build_record,
    cheat_sheet,
    checklist_problems,
    defect_hints,
    field_choices,
    field_options,
    labeling_order,
    latest_records,
    load_listings,
    load_seeds,
    match_seeds,
    reconcile,
    reconcile_checklist,
    resume_index,
    save_record,
    validate_span,
)
from wanderlint.models import Listing, Span, UnsureLabel

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = {"confirm": "Confirm", "reject": "Reject", "retype": "Change type"}
ANSWERS = {"yes": "Yes", "no": "No", "unsure": "Unsure"}


@st.cache_data
def _static() -> tuple[dict, dict, list[str], str, dict]:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    listings = load_listings(ROOT / cfg["datagen"]["output_listings"])
    seeds = load_seeds(ROOT / cfg["datagen"]["output_seeded"])
    guide = (ROOT / "labeling_guide.md").read_text(encoding="utf-8")
    return listings, seeds, labeling_order(listings), cheat_sheet(guide), defect_hints(guide)


def _labels_dir() -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return ROOT / cfg["paths"]["labels_dir"]


LISTINGS, SEEDS, ORDER, CHEAT, HINTS = _static()
LABELS_DIR = _labels_dir()


def _dname(d: str) -> str:
    return f"{d} · {HINTS[d][0]}"


def _fmt_secs(s: float) -> str:
    return f"{int(s // 60)}m {int(s % 60):02d}s"


# --- Session state ------------------------------------------------------------


def _state():
    s = st.session_state
    if "records" not in s:
        s.records = latest_records(LABELS_DIR)
        s.idx = min(resume_index(ORDER, s.records), len(ORDER) - 1)
        s.work = {}
        s.paused = False
    return s


def _new_work(lid: str, prev: LogRecord | None) -> dict:
    w = {
        "mode": prev.blind_mode if prev is not None else "checklist",
        "checklist": {},  # d -> {"answer", "fields", "reason"}
        "drops": {},  # d -> {"reason", "note"}
        "human_spans": {},  # d -> [Span] entered in reconcile
        "blind": [],
        "unsure": [],
        "revealed": False,
        "timers": {"blind": PhaseTimer(), "reconcile": PhaseTimer()},
        "outcomes": {},
        "dropped": set(),
        "removed": [],
        "prev": prev,
    }
    if prev is not None and prev.blind_mode == "checklist":
        w["checklist"] = {a.defect_id: a.model_dump() for a in prev.checklist}
        w["drops"] = {dr.defect_id: dr.model_dump() for dr in prev.drop_reasons}
        w["revealed"] = True
        w["outcomes"] = {o.defect_id: o.model_dump() for o in prev.seed_outcomes}
        seed_spans = {g.defect_id: g.spans for g in prev.seeds}
        for g in prev.final_labels:
            extra = [x for x in g.spans if x not in seed_spans.get(g.defect_id, [])]
            if extra:
                w["human_spans"][g.defect_id] = extra
        final = {g.defect_id: g.spans for g in prev.final_labels}
        w["removed"] = [
            (d, x) for d, sps in seed_spans.items() if d in final for x in sps if x not in final[d]
        ]
    elif prev is not None:  # free-mode re-edit: blind pass is locked, only reconcile changes
        w["blind"] = list(prev.blind_labels)
        w["unsure"] = list(prev.unsure)
        w["revealed"] = True
        w["outcomes"] = {o.defect_id: o.model_dump() for o in prev.seed_outcomes}
        w["dropped"] = set(prev.dropped_blind)
        try:
            full, _ = reconcile(
                lid, prev.blind_labels, prev.seeds, prev.seed_outcomes, prev.dropped_blind
            )
            kept = [(g.defect_id, s) for g in prev.final_labels for s in g.spans]
            w["removed"] = [
                (g.defect_id, s) for g in full for s in g.spans if (g.defect_id, s) not in kept
            ]
        except ReconcileError:
            pass
    return w


def _work(s, lid: str) -> dict:
    if lid not in s.work:
        s.work[lid] = _new_work(lid, s.records.get(lid))
    return s.work[lid]


def _timer(w: dict) -> PhaseTimer:
    return w["timers"]["reconcile" if w["revealed"] else "blind"]


def _go(i: int) -> None:
    s = st.session_state
    lid = ORDER[s.idx]
    if lid in s.work:
        _timer(s.work[lid]).pause(time.monotonic())
    s.idx = max(0, min(i, len(ORDER) - 1))


def _jump() -> None:
    _go(st.session_state.jump)


# --- Rendering ----------------------------------------------------------------


def _text(label: str, value: str) -> None:
    body = html.escape(value) if value.strip() else "<i style='opacity:.6'>(empty)</i>"
    st.markdown(f"**{label}**")
    st.markdown(
        f"<div style='white-space:pre-wrap;margin:-0.5rem 0 0.8rem'>{body}</div>",
        unsafe_allow_html=True,
    )


def _items(label: str, items: list[str]) -> None:
    st.markdown(f"**{label}**")
    if not items:
        st.markdown("<i style='opacity:.6'>(empty)</i>", unsafe_allow_html=True)
        return
    lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    st.markdown(f"<ul style='margin-top:-0.5rem'>{lis}</ul>", unsafe_allow_html=True)


def render_listing(lst: Listing) -> None:
    _text("title", lst.title)
    _text("short_description", lst.short_description)
    _text("description", lst.description)
    c1, c2 = st.columns(2)
    with c1:
        _text("duration", lst.duration)
        _text("meeting_point", lst.meeting_point)
        _text("category", lst.category)
        _items("tags", lst.tags)
    with c2:
        _items("inclusions", lst.inclusions)
        _items("exclusions", lst.exclusions)
        _text("cancellation_policy", lst.cancellation_policy)
    st.markdown("**attributes**")
    if lst.attributes:
        rows = "".join(
            f"<tr><td><code>{html.escape(k)}</code></td><td>"
            f"{html.escape(str(v).lower() if isinstance(v, bool) else str(v))}</td></tr>"
            for k, v in sorted(lst.attributes.items())
        )
        st.markdown(f"<table>{rows}</table>", unsafe_allow_html=True)
    else:
        st.markdown("<i style='opacity:.6'>(none)</i>", unsafe_allow_html=True)


def _span_line(sp: Span) -> str:
    shown = sp.span if sp.span else "(empty field)"
    return f"`{sp.field}`: “{shown}”"


def render_blind_free(w: dict) -> None:
    """Read-only view of a legacy free-mode blind pass (records saved before the checklist)."""
    st.subheader("Your labels (free-form blind pass, locked)")
    by_type = blind_by_type(w["blind"])
    if not by_type:
        st.caption("No blind labels.")
    for d, spans in by_type.items():
        st.markdown(f"**{_dname(d)}**")
        for sp in spans:
            st.markdown(_span_line(sp))


def _answers(w: dict) -> list[ChecklistAnswer]:
    return [ChecklistAnswer(**a) for a in w["checklist"].values() if a.get("answer")]


def _answer_line(a: dict | None) -> str:
    if not a or not a.get("answer"):
        return "—"
    if a["answer"] == "yes":
        return "**Yes** · " + ", ".join(f"`{f}`" for f in a["fields"])
    if a["answer"] == "unsure":
        return f"**Unsure** · {a['reason']}"
    return "No"


def render_checklist(lid: str, lst: Listing, w: dict) -> None:
    if w["revealed"]:
        st.subheader("Your checklist (blind pass, locked)")
        for d in DEFECT_IDS:
            st.markdown(f"{_dname(d)}: {_answer_line(w['checklist'].get(d))}")
        return
    st.subheader("Blind checklist")
    st.caption("Answer every type. Yes: pick the field(s), no span needed. Unsure: one line why.")
    for d in DEFECT_IDS:
        a = w["checklist"].setdefault(
            d, {"defect_id": d, "answer": None, "fields": [], "reason": ""}
        )
        with st.container(border=True):
            st.markdown(f"**{_dname(d)}**")
            st.caption(HINTS[d][1])
            opts = list(ANSWERS)
            a["answer"] = st.radio(
                d,
                opts,
                index=opts.index(a["answer"]) if a["answer"] in opts else None,
                format_func=ANSWERS.get,
                horizontal=True,
                label_visibility="collapsed",
                key=f"{lid}:ans:{d}",
            )
            if a["answer"] == "yes":
                a["fields"] = st.multiselect(
                    "Field(s)", field_options(lst), default=a["fields"], key=f"{lid}:flds:{d}"
                )
            else:
                a["fields"] = []
            if a["answer"] == "unsure":
                a["reason"] = st.text_input(
                    "Why unsure (one line)", value=a["reason"], key=f"{lid}:unsure:{d}"
                ).strip()
            else:
                a["reason"] = ""


def _span_adder(lid: str, lst: Listing, d: str, spans: list[Span], fields: list[str]) -> None:
    """Enter verbatim spans for a human-added label (same validation as everywhere)."""
    for j, sp in enumerate(list(spans)):
        c1, c2 = st.columns([10, 1])
        c1.markdown(_span_line(sp))
        if c2.button("✕", key=f"{lid}:hsrm:{d}:{j}", help="Remove this span"):
            spans.remove(sp)
            st.rerun()
    opts = field_options(lst)
    preferred = [f for f in fields if f in opts]
    ordered = preferred + [f for f in opts if f not in preferred]
    field = st.selectbox("Span field", ordered, key=f"{lid}:hsf:{d}")
    choices = field_choices(lst, field)
    with st.form(f"{lid}:hs_form:{d}", clear_on_submit=True, border=False):
        if choices is None:
            raw = st.text_area("Span (verbatim; empty only if the field is empty)", height=68)
            value = raw.strip()
        else:
            value = st.selectbox("Value", choices, format_func=lambda v: v or "(empty)")
        if st.form_submit_button("Add span"):
            sp = Span(field=field, span=value)
            err = validate_span(lst, sp)
            if err:
                st.error(err)
            elif sp in spans:
                st.warning("Already added.")
            else:
                spans.append(sp)
                st.rerun()


def render_unsure(lid: str, w: dict) -> None:
    with st.expander(f"Unsure ({len(w['unsure'])})", expanded=bool(w["unsure"])):
        for j, u in enumerate(w["unsure"]):
            c1, c2 = st.columns([10, 1])
            c1.markdown(f"**{', '.join(u.candidate_defect_ids)}**: {u.reason}")
            if c2.button("✕", key=f"{lid}:urm:{j}"):
                w["unsure"].pop(j)
                st.rerun()
        with st.form(f"{lid}:unsure_form", clear_on_submit=True, border=False):
            cands = st.multiselect("Candidate defect type(s)", DEFECT_IDS, format_func=_dname)
            reason = st.text_input("Reason")
            if st.form_submit_button("Add unsure"):
                if not cands or not reason.strip():
                    st.error("Pick at least one candidate and give a reason.")
                else:
                    w["unsure"].append(
                        UnsureLabel(
                            listing_id=lid, candidate_defect_ids=cands, reason=reason.strip()
                        )
                    )
                    st.rerun()


def _outcomes(w: dict) -> list[SeedOutcome]:
    return [SeedOutcome(**o) for o in w["outcomes"].values() if o.get("action")]


def _seed_decision(lid: str, d: str, w: dict) -> None:
    o = w["outcomes"].setdefault(d, {"defect_id": d, "action": None})
    opts = list(ACTIONS)
    action = st.radio(
        "Seed decision",
        opts,
        index=opts.index(o["action"]) if o.get("action") in opts else None,
        format_func=ACTIONS.get,
        horizontal=True,
        key=f"{lid}:act:{d}",
    )
    o["action"] = action
    if action == "retype":
        others = [x for x in DEFECT_IDS if x != d]
        cur = o.get("new_defect_id")
        o["new_defect_id"] = st.selectbox(
            "Change to",
            others,
            index=others.index(cur) if cur in others else 0,
            format_func=_dname,
            key=f"{lid}:new:{d}",
        )
    else:
        o["new_defect_id"] = None
    if action in ("reject", "retype"):
        o["reason"] = st.text_input(
            "Reason" + (" (required)" if action == "reject" else " (optional)"),
            value=o.get("reason", ""),
            key=f"{lid}:why:{d}",
        )
    else:
        o["reason"] = ""


def _drops(w: dict) -> list[DropReason]:
    return [DropReason(**v) for v in w["drops"].values()]


def render_reconcile_checklist(s, lid: str, lst: Listing, w: dict) -> None:
    seeds = SEEDS.get(lid, [])
    by_seed = {x.defect_id: x for x in seeds}
    st.subheader("Seeds vs your checklist")
    if not seeds:
        st.info("No seeded defects for this listing.")
    shown = [d for d in DEFECT_IDS if d in by_seed or w["checklist"][d]["answer"] != "no"]
    for d in shown:
        seed, a = by_seed.get(d), w["checklist"][d]
        with st.container(border=True):
            st.markdown(f"**{_dname(d)}**")
            c1, c2 = st.columns(2)
            with c1:
                st.caption("Seed")
                if seed is None:
                    st.markdown("—")
                for sp in seed.spans if seed else []:
                    st.markdown(_span_line(sp))
            with c2:
                st.caption("Your blind answer")
                st.markdown(_answer_line(a))
            if a["answer"] == "yes":
                drop = st.checkbox(f"Drop my {d} Yes", value=d in w["drops"], key=f"{lid}:drop:{d}")
                if drop:
                    dr = w["drops"].setdefault(d, {"defect_id": d, "reason": None, "note": ""})
                    codes = list(DROP_REASONS)
                    dr["reason"] = st.radio(
                        "Why drop it?",
                        codes,
                        index=codes.index(dr["reason"]) if dr["reason"] in codes else None,
                        format_func=DROP_REASONS.get,
                        key=f"{lid}:dropwhy:{d}",
                    )
                    if dr["reason"] == "other":
                        dr["note"] = st.text_input(
                            "Other: describe", value=dr["note"], key=f"{lid}:dropnote:{d}"
                        ).strip()
                    else:
                        dr["note"] = ""
                else:
                    w["drops"].pop(d, None)
            if seed is not None:
                _seed_decision(lid, d, w)
            if a["answer"] == "yes" and d not in w["drops"]:
                needs = seed is None or w["outcomes"].get(d, {}).get("action") != "confirm"
                st.caption(
                    "Spans for your label (required: no confirmed seed)"
                    if needs
                    else "Extra spans (optional: the seed's spans are used)"
                )
                spans = w["human_spans"].setdefault(d, [])
                _span_adder(lid, lst, d, spans, a["fields"])

    st.subheader("Result")
    outcomes = _outcomes(w)
    if any(dr.get("reason") is None for dr in w["drops"].values()):
        st.error("Pick a reason for every dropped Yes.")
        return
    try:
        args = (lid, _answers(w), seeds, outcomes, _drops(w), w["human_spans"])
        full, _ = reconcile_checklist(*args)
        final, rejected = reconcile_checklist(*args, w["removed"])
    except ReconcileError as e:
        st.error(str(e))
        return
    _render_result(lid, w, full, rejected)
    if st.button("Save & next", type="primary", key=f"{lid}:save"):
        now = time.monotonic()
        rec = build_record(
            lid,
            blind=[],
            unsure=[],
            seeds=seeds,
            outcomes=outcomes,
            dropped_blind=(),
            removed_spans=w["removed"],
            blind_seconds=w["timers"]["blind"].elapsed(now),
            reconcile_seconds=w["timers"]["reconcile"].elapsed(now),
            saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            previous=w["prev"],
            blind_mode="checklist",
            checklist=_answers(w),
            drop_reasons=_drops(w),
            human_spans=w["human_spans"],
        )
        _save(s, lid, rec)


def _render_result(lid: str, w: dict, full: list, rejected: list) -> None:
    if not full:
        st.success("Clean: no gold labels for this listing.")
    for g in full:
        st.markdown(f"**{_dname(g.defect_id)}** · `{g.label_source}`")
        for j, sp in enumerate(g.spans):
            keep = st.checkbox(
                _span_line(sp),
                value=(g.defect_id, sp) not in w["removed"],
                key=f"{lid}:keep:{g.defect_id}:{j}",
            )
            key = (g.defect_id, sp)
            if not keep and key not in w["removed"]:
                w["removed"].append(key)
                st.rerun()
            if keep and key in w["removed"]:
                w["removed"].remove(key)
                st.rerun()
    for r in rejected:
        st.markdown(f"Rejected seed **{r.defect_id}**: {r.reason}")


def _save(s, lid: str, rec: LogRecord) -> None:
    s.records = save_record(LABELS_DIR, rec)
    del s.work[lid]
    _go(s.idx + 1)
    st.rerun()


def render_reconcile(s, lid: str, lst: Listing, w: dict) -> None:
    """Legacy free-mode reconcile (re-edits of records saved before the checklist)."""
    seeds = SEEDS.get(lid, [])
    st.subheader("Seeds vs your labels")
    pairs = match_seeds(w["blind"], seeds)
    if not seeds:
        st.info("No seeded defects for this listing.")
    for d, (seed, mine) in pairs.items():
        with st.container(border=True):
            st.markdown(f"**{_dname(d)}**")
            c1, c2 = st.columns(2)
            with c1:
                st.caption("Seed")
                if seed is None:
                    st.markdown("—")
                else:
                    for sp in seed.spans:
                        st.markdown(_span_line(sp))
            with c2:
                st.caption("Yours (blind)")
                if mine is None:
                    st.markdown("—")
                else:
                    for sp in mine:
                        st.markdown(_span_line(sp))
                    drop = st.checkbox(
                        f"Drop my {d} label", value=d in w["dropped"], key=f"{lid}:drop:{d}"
                    )
                    (w["dropped"].add if drop else w["dropped"].discard)(d)
            if seed is not None:
                _seed_decision(lid, d, w)
    render_unsure(lid, w)

    st.subheader("Result")
    outcomes = _outcomes(w)
    try:
        full, _ = reconcile(lid, w["blind"], seeds, outcomes, w["dropped"])
        final, rejected = reconcile(lid, w["blind"], seeds, outcomes, w["dropped"], w["removed"])
    except ReconcileError as e:
        st.error(str(e))
        return
    _render_result(lid, w, full, rejected)
    if st.button("Save & next", type="primary", key=f"{lid}:save"):
        now = time.monotonic()
        rec = build_record(
            lid,
            blind=w["blind"],
            unsure=w["unsure"],
            seeds=seeds,
            outcomes=outcomes,
            dropped_blind=w["dropped"],
            removed_spans=w["removed"],
            blind_seconds=w["timers"]["blind"].elapsed(now),
            reconcile_seconds=w["timers"]["reconcile"].elapsed(now),
            saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            previous=w["prev"],
        )
        _save(s, lid, rec)


def sidebar(s) -> None:
    with st.sidebar:
        n = len(s.records)
        st.progress(n / len(ORDER), text=f"{n}/{len(ORDER)} labeled")
        c1, c2, c3 = st.columns(3)
        c1.button("◀ Prev", on_click=_go, args=(s.idx - 1,), disabled=s.idx == 0)
        c2.button("Next ▶", on_click=_go, args=(s.idx + 1,), disabled=s.idx == len(ORDER) - 1)
        c3.button(
            "Resume",
            on_click=_go,
            args=(resume_index(ORDER, s.records),),
            help="First listing not yet labeled",
        )
        st.session_state.jump = s.idx
        st.selectbox(
            "Jump to",
            list(range(len(ORDER))),
            format_func=lambda i: f"{i + 1}. {ORDER[i]}" + (" ✓" if ORDER[i] in s.records else ""),
            key="jump",
            on_change=_jump,
        )
        with st.expander("Cheat sheet", expanded=True):
            st.markdown(CHEAT)


def main() -> None:
    st.set_page_config(page_title="Wanderlint labeling", layout="wide")
    s = _state()
    sidebar(s)
    lid = ORDER[s.idx]
    lst = LISTINGS[lid]
    w = _work(s, lid)
    timer = _timer(w)
    now = time.monotonic()
    if not s.paused:
        timer.start(now)

    phase = "Reconcile" if w["revealed"] else "Blind pass"
    head, tbtn = st.columns([6, 1])
    status = " · edited" if w["prev"] is not None else ""
    head.markdown(
        f"### {s.idx + 1}/{len(ORDER)} · `{lid}` · locale **{lst.locale}**{status}  \n"
        f"{phase} · active {_fmt_secs(timer.elapsed(now))} (updates on each action)"
    )
    if tbtn.button("Resume ▶" if s.paused else "Pause ⏸", key="pause"):
        (timer.start if s.paused else timer.pause)(now)
        s.paused = not s.paused
        st.rerun()
    if timer.elapsed(now) >= PHASE_CAP_S:
        st.warning("This phase passed 10 minutes of active time; the record will be time-capped.")
    if s.paused:
        st.info("Paused. The listing is hidden and the timer is stopped.")
        return

    left, right = st.columns([3, 2], gap="large")
    with left:
        render_listing(lst)
    with right:
        if w["mode"] == "free":  # legacy record: locked blind view + free-mode reconcile
            render_blind_free(w)
            render_reconcile(s, lid, lst, w)
            return
        render_checklist(lid, lst, w)
        if not w["revealed"]:
            problems = checklist_problems(_answers(w))
            st.caption(f"Answered {8 - sum('not answered' in p for p in problems)}/8")
            for p in problems:
                st.caption(f"• {p}")
            if st.button(
                "Reveal seeds", type="primary", key=f"{lid}:reveal", disabled=bool(problems)
            ):
                t = time.monotonic()
                w["timers"]["blind"].pause(t)
                w["revealed"] = True
                w["timers"]["reconcile"].start(t)
                st.rerun()
        else:
            render_reconcile_checklist(s, lid, lst, w)


main()
