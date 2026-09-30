import os, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nodaris_harness import router as R  # noqa: E402


@pytest.mark.parametrize("prompt,playbook", [
    ("the search filter resets when I change pages", "bug-fix"),
    ("can you pentest my application", "security-check"),
    ("build me a patient intake app for a pain clinic", "new-app"),
    ("parse the 835 and post the payments", "rcm-data"),
    ("why does the claims page take 8 seconds to load", "perf"),
    ("how does tenant scoping work in the claims view?", "investigate"),
    ("add an export button to the remits table", "feature"),
    ("rename PayerMap to PayerMaster everywhere", "refactor"),
    ("break this down into phases for the team", "plan"),
    ("ship the intake branch", "ship"),
    ("thanks!", None),
])
def test_routes(prompt, playbook):
    assert R.route(prompt)["playbook"] == playbook


def test_overlays_and_brief():
    d = R.route("add a login page that shows the patient's balance")
    assert d["playbook"] == "feature" and set(d["overlays"]) >= {"phi", "auth", "tenant-money"}
    b = R.brief(d)
    assert b.startswith("Harness router: this is feature work") and "Overlay:" in b and "1. " in b
    assert R.brief(R.route("ok")) == ""


def test_questions_about_rcm_documents_are_investigations_with_the_rcm_rules():
    d = R.route("how does the 835 parser decide which claim a service line belongs to?")
    assert d["playbook"] == "investigate" and "rcm" in d["overlays"]


def test_a_crash_in_rcm_code_is_a_bug_fix_with_the_rcm_rules():
    d = R.route("the 835 upload crashes on weird files")
    assert d["playbook"] == "bug-fix" and {"rcm", "hardening"} <= set(d["overlays"])


def test_building_rcm_features_uses_the_rcm_playbook():
    assert R.route("parse the 835 files from Metro")["playbook"] == "rcm-data"


def test_the_brief_names_the_risk_and_the_gates_up_front():
    b = R.brief(R.route("add a refund button for practice admins"))
    assert "Risk: high" in b and "design record" in b and "independent review" in b
    assert "Risk: low" in R.brief(R.route("rename the helper in utils"))


def test_video_and_motion_requests_route_to_the_media_playbook():
    for q in ("make a launch video of the claims app", "animate the hero on scroll", "turn this site into a reel"):
        assert R.route(q)["playbook"] == "media", q
    assert "product-film" in R.brief(R.route("make a launch video of the claims app"))


def test_a_broken_animation_is_still_a_bug_fix():
    assert R.route("the dashboard animation is broken on Safari")["playbook"] == "bug-fix"


def test_autonomous_runs_need_a_spec_first():
    d = R.route("build the whole remittance screen overnight while I'm away")
    assert "autonomous" in d["overlays"] and "docs/specs/" in R.brief(d)


def test_creative_requests_reach_the_creative_studio():
    from nodaris_harness import router as r
    for text in ["make a dark cinematic video of these car photos with music",
                 "trim this clip and add captions for the video",
                 "add a splash screen and a spinner to our CLI",
                 "build a scroll-driven parallax hero with gsap"]:
        d = r.route(text, use_profile=False)
        assert d["playbook"] == "media", text
        assert "creative-studio" in r.brief(d, "nodaris-harness")
