"""Streamlit labeling tool: blind-then-reveal (labeling_guide.md §1, §6).

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
    PHASE_CAP_S,
    BlindLabel,
    LogRecord,
    PhaseTimer,
    ReconcileError,
    SeedOutcome,
    blind_by_type,
    build_record,
    cheat_sheet,
    defect_hints,
    field_choices,
    field_options,
    labeling_order,
    latest_records,
    load_listings,
    load_seeds,
    match_seeds,
    reconcile,
    resume_index,
    save_record,
    validate_span,
)
from wanderlint.models import Listing, Span, UnsureLabel

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = {"confirm": "Confirm", "reject": "Reject", "retype": "Change type"}


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
        "blind": [],
        "unsure": [],
        "revealed": False,
        "timers": {"blind": PhaseTimer(), "reconcile": PhaseTimer()},
        "outcomes": {},
        "dropped": set(),
        "removed": [],
        "prev": prev,
    }
    if prev is not None:  # re-edit: blind pass is locked, only reconcile changes
        w["blind"] = list(prev.blind_labels)
        w["unsure"] = list(prev.unsure)
        w["revealed"] = True
        w["outcomes"] = {o.defect_id: o.model_dump() for o in prev.seed_outcomes}
        w["dropped"] = set(prev.dropped_blind)
        try:
            full, _ = reconcile(
                lid, prev.blind_labels, prev.seeds, prev.seed_outcomes, prev.dropped_blind
            )
            kept = {(g.defect_id, s) for g in prev.final_labels for s in g.spans}
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


def render_blind(lid: str, lst: Listing, w: dict) -> None:
    locked = w["prev"] is not None
    st.subheader("Your labels" + (" (blind pass, locked)" if locked else " (blind pass)"))
    by_type = blind_by_type(w["blind"])
    if not by_type:
        st.caption("No labels yet. A clean listing is a valid answer.")
    for d, spans in by_type.items():
        st.markdown(f"**{_dname(d)}**")
        for j, sp in enumerate(spans):
            c1, c2 = st.columns([10, 1])
            c1.markdown(_span_line(sp))
            if not locked and c2.button("✕", key=f"{lid}:rm:{d}:{j}", help="Remove this span"):
                w["blind"] = [
                    BlindLabel(defect_id=b.defect_id, spans=[x for x in b.spans if x != sp])
                    for b in w["blind"]
                    if b.defect_id != d or [x for x in b.spans if x != sp]
                ]
                st.rerun()
    if locked or w["revealed"]:
        return
    with st.container(border=True):
        st.markdown("**Add a span**")
        d = st.selectbox(
            "Defect type",
            DEFECT_IDS,
            format_func=_dname,
            key=f"{lid}:d",
            help="\n\n".join(f"**{x}** {HINTS[x][0]}: {HINTS[x][1]}" for x in DEFECT_IDS),
        )
        st.caption(HINTS[d][1])
        field = st.selectbox("Field", field_options(lst), key=f"{lid}:f")
        choices = field_choices(lst, field)
        with st.form(f"{lid}:span_form", clear_on_submit=True, border=False):
            if choices is None:
                raw = st.text_area(
                    "Span (verbatim; leave empty only if the field is empty)", height=68
                )
                value = raw.strip()
            else:
                value = st.selectbox("Value", choices, format_func=lambda v: v or "(empty)")
            if st.form_submit_button("Add span"):
                sp = Span(field=field, span=value)
                err = validate_span(lst, sp)
                if err:
                    st.error(err)
                elif sp in by_type.get(d, []):
                    st.warning("Already added.")
                else:
                    w["blind"].append(BlindLabel(defect_id=d, spans=[sp]))
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


def render_reconcile(s, lid: str, lst: Listing, w: dict) -> None:
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
    render_unsure(lid, w)

    st.subheader("Result")
    outcomes = _outcomes(w)
    try:
        full, _ = reconcile(lid, w["blind"], seeds, outcomes, w["dropped"])
        final, rejected = reconcile(lid, w["blind"], seeds, outcomes, w["dropped"], w["removed"])
    except ReconcileError as e:
        st.error(str(e))
        return
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
        s.records = save_record(LABELS_DIR, rec)
        del s.work[lid]
        _go(s.idx + 1)
        st.rerun()


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
        render_blind(lid, lst, w)
        if not w["revealed"]:
            render_unsure(lid, w)
            label = "Reveal seeds" if w["blind"] else "Clean: reveal seeds"
            if st.button(label, type="primary", key=f"{lid}:reveal"):
                t = time.monotonic()
                w["timers"]["blind"].pause(t)
                w["revealed"] = True
                w["timers"]["reconcile"].start(t)
                st.rerun()
        else:
            render_reconcile(s, lid, lst, w)


main()
