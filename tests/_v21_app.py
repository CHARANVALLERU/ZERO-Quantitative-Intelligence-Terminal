"""Minimal Streamlit entry point for the v2.1 panel smoke test.

`AppTest.from_function` needs real source on disk, so the panel is mounted from
this file instead of a lambda.
"""
from ui.v21_panel import render_v21_panel

render_v21_panel()
