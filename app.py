
"""
app.py
======
MeetingMind AI -- Streamlit dashboard.
 
Ties together auth, calendar, transcription, the meeting agent, and the
database into a single SaaS-style UI:
 
    Sidebar:  Google Login / Guest Mode, Upcoming Meetings, History
    Main:     Upload -> Analyze -> Progress -> Transcript -> Summary
              Cards -> Action Items Table -> Downloads
 
Run with:
    streamlit run app.py
"""
 
from __future__ import annotations
 
from pathlib import Path
 
import streamlit as st
 
from auth import AuthError, GoogleAuthenticator, guest_login
from calendar_service import CalendarError, CalendarService
from config import OUTPUT_DIR, TEMP_DIR, UPLOAD_DIR, logger, settings
from database import Database, DatabaseError
from meeting_agent import MeetingAgent, MeetingAnalysisError
from models import MeetingRecord
from transcriber import Transcriber, TranscriptionError
from utils import (
    analysis_to_json,
    analysis_to_markdown,
    analysis_to_txt,
    format_file_size,
    is_upload_size_valid,
    safe_filename,
)
 
st.set_page_config(
    page_title="MeetingMind AI",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)
 
# ----------------------------------------------------------------------
# Session state defaults
# ----------------------------------------------------------------------
_DEFAULTS = {
    "user": None,
    "current_record": None,
    "auth_flow": None,
}
for key, value in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value
 
 
# ----------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------
# Design language: "signal from noise" -- MeetingMind turns raw audio into
# structured signal, so the signature motif is an animated waveform that
# resolves into steady bars. Palette is a dark ink surface with a teal
# "signal" accent and a warm coral used sparingly for urgency/priority.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
 
    :root {
        --mm-bg: #0e1117;
        --mm-surface: #171b24;
        --mm-surface-hi: #1f2430;
        --mm-border: rgba(255,255,255,0.08);
        --mm-signal: #2dd4bf;
        --mm-signal-soft: rgba(45,212,191,0.15);
        --mm-warm: #fb923c;
        --mm-danger: #f87171;
        --mm-text: #e8eaf0;
        --mm-muted: #8992a6;
    }
 
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    code, .stCodeBlock, .mm-mono { font-family: 'JetBrains Mono', monospace; }
 
    /* ---------- keyframes ---------- */
    @keyframes mmFadeUp {
        from { opacity: 0; transform: translateY(14px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes mmWave {
        0%, 100% { transform: scaleY(0.35); }
        50%      { transform: scaleY(1); }
    }
    @keyframes mmPulse {
        0%, 100% { box-shadow: 0 0 0 0 var(--mm-signal-soft); }
        50%      { box-shadow: 0 0 0 6px rgba(45,212,191,0); }
    }
    @keyframes mmPulseWarm {
        0%, 100% { box-shadow: 0 0 0 0 rgba(251,146,60,0.35); }
        50%      { box-shadow: 0 0 0 6px rgba(251,146,60,0); }
    }
    @keyframes mmShimmer {
        0%   { background-position: -200% 0; }
        100% { background-position: 200% 0; }
    }
    @keyframes mmGradientShift {
        0%   { background-position: 0% 50%; }
        50%  { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    @keyframes mmSpin { to { transform: rotate(360deg); } }
 
    /* ---------- hero header ---------- */
    .mm-hero {
        display: flex; align-items: center; gap: 0.9rem;
        animation: mmFadeUp 0.5s ease-out both;
        margin-bottom: 0.1rem;
    }
    .mm-wave { display: flex; align-items: flex-end; gap: 3px; height: 34px; }
    .mm-wave span {
        display: block; width: 4px; border-radius: 3px;
        background: linear-gradient(180deg, var(--mm-signal), #22a8ff);
        animation: mmWave 1.1s ease-in-out infinite;
        transform-origin: bottom;
    }
    .mm-wave span:nth-child(1) { height: 12px; animation-delay: 0s; }
    .mm-wave span:nth-child(2) { height: 26px; animation-delay: 0.12s; }
    .mm-wave span:nth-child(3) { height: 34px; animation-delay: 0.24s; }
    .mm-wave span:nth-child(4) { height: 18px; animation-delay: 0.36s; }
    .mm-wave span:nth-child(5) { height: 28px; animation-delay: 0.48s; }
    .mm-title {
        font-size: 2.1rem; font-weight: 800; letter-spacing: -0.02em;
        background: linear-gradient(90deg, #ffffff 0%, var(--mm-signal) 45%, #22a8ff 90%);
        background-size: 200% auto;
        -webkit-background-clip: text; background-clip: text; color: transparent;
        animation: mmGradientShift 6s ease-in-out infinite;
    }
    .mm-subtitle {
        color: var(--mm-muted); font-size: 0.98rem; margin-top: 0.15rem;
        animation: mmFadeUp 0.6s ease-out 0.1s both;
    }
 
    /* ---------- metric / stat cards ---------- */
    .metric-card {
        background: linear-gradient(160deg, var(--mm-surface-hi), var(--mm-surface));
        border: 1px solid var(--mm-border);
        border-radius: 14px;
        padding: 1rem 1.25rem;
        transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        animation: mmFadeUp 0.45s ease-out both;
    }
    .metric-card:hover {
        transform: translateY(-3px);
        border-color: var(--mm-signal);
        box-shadow: 0 10px 30px -12px rgba(45,212,191,0.35);
    }
    .stat-label { font-size: 0.75rem; opacity: 0.65; text-transform: uppercase; letter-spacing: .06em; }
    .stat-value { font-size: 1.5rem; font-weight: 700; margin-top: 2px; color: var(--mm-text); }
 
    /* ---------- badges / pills ---------- */
    .mm-badge {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 3px 11px; border-radius: 999px;
        font-size: 0.78rem; font-weight: 600; letter-spacing: 0.01em;
        border: 1px solid var(--mm-border);
        animation: mmFadeUp 0.4s ease-out both;
    }
    .mm-badge.signal { background: var(--mm-signal-soft); color: var(--mm-signal); border-color: rgba(45,212,191,0.35); }
    .mm-badge.warm { background: rgba(251,146,60,0.15); color: var(--mm-warm); border-color: rgba(251,146,60,0.35); }
    .mm-badge.danger { background: rgba(248,113,113,0.15); color: var(--mm-danger); border-color: rgba(248,113,113,0.35); animation: mmFadeUp 0.4s ease-out both, mmPulseWarm 1.8s ease-in-out infinite; }
    .mm-badge.neutral { background: rgba(255,255,255,0.06); color: var(--mm-muted); }
    .mm-badge .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
 
    /* ---------- step tracker (upload -> transcribe -> analyze -> save) ---------- */
    .mm-steps { display: flex; align-items: center; gap: 0; margin: 0.6rem 0 1.1rem 0; }
    .mm-step { display: flex; align-items: center; gap: 8px; flex: 1; }
    .mm-step .mm-dot {
        width: 26px; height: 26px; min-width: 26px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.7rem; font-weight: 700; color: var(--mm-muted);
        background: var(--mm-surface-hi); border: 2px solid var(--mm-border);
        transition: all 0.35s ease;
    }
    .mm-step .mm-label { font-size: 0.8rem; color: var(--mm-muted); transition: color 0.35s ease; white-space: nowrap; }
    .mm-step.active .mm-dot { border-color: var(--mm-signal); color: var(--mm-signal); animation: mmPulse 1.4s ease-in-out infinite; }
    .mm-step.active .mm-label { color: var(--mm-text); font-weight: 600; }
    .mm-step.done .mm-dot { background: var(--mm-signal); border-color: var(--mm-signal); color: #0e1117; }
    .mm-step.done .mm-label { color: var(--mm-signal); }
    .mm-connector { flex: 0.5; height: 2px; background: var(--mm-border); position: relative; overflow: hidden; margin: 0 2px; }
    .mm-connector.filled { background: var(--mm-signal); }
 
    /* ---------- buttons ---------- */
    div.stButton > button, div.stDownloadButton > button, div.stLinkButton > a {
        transition: transform 0.15s ease, box-shadow 0.15s ease !important;
    }
    div.stButton > button:hover, div.stDownloadButton > button:hover, div.stLinkButton > a:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 18px -8px rgba(45,212,191,0.45);
    }
    div.stButton > button[kind="primary"] {
        background: linear-gradient(90deg, var(--mm-signal), #22a8ff) !important;
        border: none !important;
        background-size: 200% auto;
        animation: mmGradientShift 4s ease-in-out infinite;
    }
 
    /* ---------- misc ---------- */
    section[data-testid="stSidebar"] { animation: mmFadeUp 0.4s ease-out both; }
    .mm-fade { animation: mmFadeUp 0.45s ease-out both; }
    </style>
    """,
    unsafe_allow_html=True,
)
 
 
# ----------------------------------------------------------------------
# Small animated UI helpers
# ----------------------------------------------------------------------
def render_hero(title: str = "MeetingMind AI", subtitle: str | None = None) -> None:
    """Animated gradient title with a small waveform flourish -- the
    app's signature element, echoing the audio-to-text pipeline."""
    st.markdown(
        f"""
        <div class="mm-hero">
            <div class="mm-wave"><span></span><span></span><span></span><span></span><span></span></div>
            <div class="mm-title">{title}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(f'<div class="mm-subtitle">{subtitle}</div>', unsafe_allow_html=True)
 
 
def badge(text: str, kind: str = "neutral") -> str:
    """Return an animated pill badge. kind: signal | warm | danger | neutral"""
    return f'<span class="mm-badge {kind}"><span class="dot"></span>{text}</span>'
 
 
_PRIORITY_KIND = {"Critical": "danger", "High": "warm", "Medium": "neutral", "Low": "neutral"}
_SENTIMENT_KIND = {"Positive": "signal", "Neutral": "neutral", "Negative": "danger", "Mixed": "warm"}
 
_PIPELINE_STEPS = ["Upload", "Transcribe", "Analyze", "Save"]
 
 
def render_step_tracker(placeholder, active_index: int, done_upto: int = -1) -> None:
    """Render a horizontal animated step tracker into `placeholder`.
 
    active_index: index (0-based) of the step currently in progress (pulses).
    done_upto: index of the last completed step (filled solid); -1 = none yet.
    """
    parts = []
    for i, label in enumerate(_PIPELINE_STEPS):
        state = "done" if i <= done_upto else ("active" if i == active_index else "")
        dot_content = "✓" if i <= done_upto else str(i + 1)
        parts.append(
            f'<div class="mm-step {state}"><div class="mm-dot">{dot_content}</div>'
            f'<div class="mm-label">{label}</div></div>'
        )
        if i < len(_PIPELINE_STEPS) - 1:
            filled = "filled" if i < done_upto or (i == done_upto) else ""
            parts.append(f'<div class="mm-connector {filled}"></div>')
    placeholder.markdown(f'<div class="mm-steps">{"".join(parts)}</div>', unsafe_allow_html=True)
 
 
# ----------------------------------------------------------------------
# Sidebar: Auth
# ----------------------------------------------------------------------
def render_login_sidebar() -> None:
    st.sidebar.title("🧠 MeetingMind AI")
    st.sidebar.caption("AI-powered meeting notes agent")
    st.sidebar.divider()
    st.sidebar.subheader("Sign in")
 
    if not settings.google_oauth_configured():
        st.sidebar.info("Google OAuth not configured. Continuing in Guest Mode only.")
        if st.sidebar.button("Continue as Guest", use_container_width=True):
            st.session_state.user = guest_login()
            st.rerun()
        return
 
    query_params = st.query_params
    if "code" in query_params and st.session_state.user is None:
        authenticator: GoogleAuthenticator = st.session_state.auth_flow
        if authenticator is None:
            authenticator = GoogleAuthenticator()
            authenticator.get_authorization_url()  # rebuild flow deterministically
            st.session_state.auth_flow = authenticator
        try:
            # Rebuild the exact response URL Google redirected to, since
            # google-auth-oauthlib needs the full URL (including query
            # string) to complete the token exchange.
            import urllib.parse as _up
 
            qs = _up.urlencode(dict(query_params))
            response_url = f"{settings.google_redirect_uri}?{qs}"
            user = authenticator.exchange_code(response_url)
            st.session_state.user = user
            st.query_params.clear()
            st.rerun()
        except AuthError as exc:
            st.sidebar.error(str(exc))
 
    if st.sidebar.button("Sign in with Google", use_container_width=True):
        authenticator = GoogleAuthenticator()
        auth_url = authenticator.get_authorization_url()
        st.session_state.auth_flow = authenticator
        st.sidebar.link_button("Click to authorize →", auth_url, use_container_width=True)
 
    st.sidebar.divider()
    if st.sidebar.button("Continue as Guest", use_container_width=True):
        st.session_state.user = guest_login()
        st.rerun()
 
 
def render_upcoming_meetings() -> None:
    user = st.session_state.user
    st.sidebar.subheader("📅 Upcoming Meetings")
    if user.is_guest or user.credentials is None:
        st.sidebar.caption("Sign in with Google to see your calendar.")
        return
    try:
        service = CalendarService(user.credentials)
        today = service.get_todays_meetings()
        upcoming = service.get_upcoming_meetings(days_ahead=7)
    except CalendarError as exc:
        st.sidebar.warning(f"Calendar unavailable: {exc}")
        return
 
    if today:
        st.sidebar.caption("Today")
        for ev in today:
            st.sidebar.write(f"• {ev.summary}")
    if upcoming:
        st.sidebar.caption("Next 7 days")
        for ev in upcoming[:5]:
            when = ev.start.strftime("%a %b %d, %H:%M") if ev.start else "TBD"
            st.sidebar.write(f"• {ev.summary} — {when}")
    if not today and not upcoming:
        st.sidebar.caption("No upcoming meetings found.")
 
 
def render_history_sidebar() -> None:
    user = st.session_state.user
    st.sidebar.subheader("🕘 History")
 
    if settings.missing_persistence_vars():
        st.sidebar.caption("Database not configured — history disabled.")
        return
 
    db = Database()
    search_query = st.sidebar.text_input("Search history", key="history_search", placeholder="Search meetings...")
    try:
        records = (
            db.search_meetings(user.email, search_query)
            if search_query
            else db.get_history(user.email)
        )
    except DatabaseError as exc:
        st.sidebar.error(str(exc))
        return
 
    if not records:
        st.sidebar.caption("No meetings yet. Analyze one to get started.")
        return
 
    for record in records:
        with st.sidebar.container():
            cols = st.sidebar.columns([4, 1])
            if cols[0].button(record.title or "Untitled Meeting", key=f"open_{record.id}", use_container_width=True):
                st.session_state.current_record = record
                st.rerun()
            if cols[1].button("🗑", key=f"del_{record.id}"):
                try:
                    db.delete_meeting(record.id, user.email)
                    st.rerun()
                except DatabaseError as exc:
                    st.sidebar.error(str(exc))
 
 
# ----------------------------------------------------------------------
# Main: Upload & Analyze
# ----------------------------------------------------------------------
def run_pipeline(uploaded_file, meeting_title: str) -> MeetingRecord | None:
    user = st.session_state.user
    tracker = st.empty()
    status = st.empty()
    render_step_tracker(tracker, active_index=0, done_upto=-1)
    status.markdown('<div class="mm-fade">📤 Saving upload...</div>', unsafe_allow_html=True)
 
    # 1. Save upload
    dest_path = UPLOAD_DIR / safe_filename(uploaded_file.name)
    dest_path.write_bytes(uploaded_file.getbuffer())
    render_step_tracker(tracker, active_index=1, done_upto=0)
    status.markdown(
        '<div class="mm-fade">🎙️ Extracting audio & transcribing with Faster Whisper '
        '(this can take a while on CPU)...</div>',
        unsafe_allow_html=True,
    )
 
    # 2. Transcribe
    try:
        transcriber = Transcriber()
        result = transcriber.transcribe_file(dest_path)
    except TranscriptionError as exc:
        tracker.empty()
        status.empty()
        st.error(f"Transcription failed: {exc}")
        return None
    render_step_tracker(tracker, active_index=2, done_upto=1)
    status.markdown(
        '<div class="mm-fade">🧠 Meeting Intelligence Agent is reasoning over the transcript...</div>',
        unsafe_allow_html=True,
    )
 
    # 3. Analyze
    try:
        agent = MeetingAgent()
        analysis = agent.analyze(result.text, meeting_title=meeting_title)
    except MeetingAnalysisError as exc:
        tracker.empty()
        status.empty()
        st.error(f"AI analysis failed: {exc}")
        return None
    render_step_tracker(tracker, active_index=3, done_upto=2)
    status.markdown('<div class="mm-fade">💾 Saving to your meeting history...</div>', unsafe_allow_html=True)
 
    record = MeetingRecord(
        user_email=user.email,
        title=meeting_title or uploaded_file.name,
        source_filename=uploaded_file.name,
        transcript=result.text,
        analysis=analysis,
    )
 
    # 4. Persist (non-fatal if DB isn't configured)
    if not settings.missing_persistence_vars():
        try:
            Database().insert_meeting(record)
        except DatabaseError as exc:
            st.warning(f"Analysis succeeded but could not be saved to history: {exc}")
    else:
        st.info("Database not configured — this result won't be saved to history.")
 
    render_step_tracker(tracker, active_index=3, done_upto=3)
    status.markdown('<div class="mm-fade">✅ Done.</div>', unsafe_allow_html=True)
    return record
 
 
def render_upload_section() -> None:
    st.subheader("Upload a Recording")
    col1, col2 = st.columns([3, 2])
    with col1:
        uploaded_file = st.file_uploader(
            "Meeting recording (mp4, mov, avi, wav, mp3, m4a)",
            type=["mp4", "mov", "avi", "wav", "mp3", "m4a"],
        )
    with col2:
        meeting_title = st.text_input("Meeting title (optional)", placeholder="e.g. Q3 Planning Sync")
 
    if uploaded_file is not None:
        size_bytes = uploaded_file.size
        if not is_upload_size_valid(size_bytes):
            st.error("File is empty or exceeds the 500MB limit.")
            return
        st.caption(f"Selected: {uploaded_file.name} ({format_file_size(size_bytes)})")
 
        if st.button("Analyze Meeting", type="primary"):
            record = run_pipeline(uploaded_file, meeting_title or uploaded_file.name)
            if record is not None:
                st.session_state.current_record = record
                st.success("Analysis complete.")
 
 
# ----------------------------------------------------------------------
# Main: Results display
# ----------------------------------------------------------------------
def render_results(record: MeetingRecord) -> None:
    a = record.analysis
    st.divider()
    st.header(record.title)
    sentiment_kind = _SENTIMENT_KIND.get(a.sentiment.value, "neutral")
    st.markdown(
        f'<div class="mm-fade">{badge(a.sentiment.value, sentiment_kind)} '
        f'{badge(a.category.value, "neutral")}</div>',
        unsafe_allow_html=True,
    )
    st.caption(f"Analyzed {record.created_at.strftime('%Y-%m-%d %H:%M UTC')}")
 
    m1, m2, m3, m4 = st.columns(4)
    for i, (col, label, value) in enumerate((
        (m1, "Category", a.category.value),
        (m2, "Sentiment", a.sentiment.value),
        (m3, "Action Items", str(len(a.action_items))),
        (m4, "Open Risks", str(len(a.risks) + len(a.blockers))),
    )):
        with col:
            st.markdown(
                f'<div class="metric-card" style="animation-delay:{i * 0.08}s">'
                f'<div class="stat-label">{label}</div>'
                f'<div class="stat-value">{value}</div></div>',
                unsafe_allow_html=True,
            )
 
    st.subheader("Executive Summary")
    st.write(a.executive_summary)
 
    with st.expander("Detailed Summary", expanded=False):
        st.write(a.detailed_summary)
 
    tab_decisions, tab_actions, tab_risks, tab_transcript, tab_email = st.tabs(
        ["Key Decisions", "Action Items", "Risks & Questions", "Transcript", "Follow-up Email"]
    )
 
    with tab_decisions:
        if a.key_decisions:
            for d in a.key_decisions:
                st.markdown(f"- {d}")
        else:
            st.caption("No key decisions identified.")
 
    with tab_actions:
        if a.action_items:
            rows = ['<table style="width:100%; border-collapse:collapse;">',
                    '<tr style="text-align:left; color:var(--mm-muted); font-size:0.78rem;">'
                    '<th style="padding:6px 8px;">TASK</th><th style="padding:6px 8px;">OWNER</th>'
                    '<th style="padding:6px 8px;">DUE DATE</th><th style="padding:6px 8px;">PRIORITY</th></tr>']
            for i, item in enumerate(a.action_items):
                due = item.due_date.isoformat() if item.due_date else "—"
                kind = _PRIORITY_KIND.get(item.priority.value, "neutral")
                rows.append(
                    f'<tr class="mm-fade" style="animation-delay:{i*0.05}s; '
                    f'border-top:1px solid var(--mm-border);">'
                    f'<td style="padding:9px 8px;">{item.task}</td>'
                    f'<td style="padding:9px 8px; color:var(--mm-muted);">{item.owner}</td>'
                    f'<td style="padding:9px 8px; color:var(--mm-muted);">{due}</td>'
                    f'<td style="padding:9px 8px;">{badge(item.priority.value, kind)}</td></tr>'
                )
            rows.append("</table>")
            st.markdown("".join(rows), unsafe_allow_html=True)
        else:
            st.caption("No action items identified.")
 
    with tab_risks:
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            st.markdown("**Risks**")
            st.markdown("\n".join(f"- {r}" for r in a.risks) or "_None_")
        with rc2:
            st.markdown("**Blockers**")
            st.markdown("\n".join(f"- {b}" for b in a.blockers) or "_None_")
        with rc3:
            st.markdown("**Open Questions**")
            st.markdown("\n".join(f"- {q}" for q in a.open_questions) or "_None_")
        if a.suggested_next_meeting:
            st.info(f"**Suggested next meeting:** {a.suggested_next_meeting}")
 
    with tab_transcript:
        st.text_area("Full transcript", record.transcript, height=300, label_visibility="collapsed")
 
    with tab_email:
        st.text_area("Follow-up email draft", a.follow_up_email_draft, height=250, label_visibility="collapsed")
 
    st.subheader("Download Report")
    d1, d2, d3 = st.columns(3)
    base_name = safe_filename(record.title)
    d1.download_button(
        "⬇ Markdown", analysis_to_markdown(record), file_name=f"{base_name}.md", mime="text/markdown"
    )
    d2.download_button(
        "⬇ JSON", analysis_to_json(record), file_name=f"{base_name}.json", mime="application/json"
    )
    d3.download_button(
        "⬇ TXT", analysis_to_txt(record), file_name=f"{base_name}.txt", mime="text/plain"
    )
 
 
# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def main() -> None:
    if st.session_state.user is None:
        render_login_sidebar()
        render_hero(
            subtitle="Turn meeting recordings into structured summaries, decisions, "
            "action items, and follow-up emails — automatically."
        )
        st.info("Sign in with Google or continue as a guest to get started.")
        return
 
    user = st.session_state.user
    st.sidebar.success(f"Signed in as {user.name}" + (" (Guest)" if user.is_guest else ""))
    if st.sidebar.button("Sign out"):
        st.session_state.user = None
        st.session_state.current_record = None
        st.rerun()
    st.sidebar.divider()
 
    render_upcoming_meetings()
    st.sidebar.divider()
    render_history_sidebar()
 
    render_hero(subtitle="Upload a recording and let the agent do the notetaking.")
    render_upload_section()
 
    if st.session_state.current_record is not None:
        render_results(st.session_state.current_record)
 
 
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - top-level safety net for the UI
        logger.exception("Unhandled application error.")
        st.error(f"An unexpected error occurred: {exc}")
 