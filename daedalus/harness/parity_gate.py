"""A6.5b parity gate: the real-click evidence for every row that is home in the Studio, the spec's nine phase-3 checks,
and which old HTML pages may redirect.

Evidence is a list of browser tests (``file.py::test_name``) that click the real Studio controls for that row. The test
suite runs them; ``tests/test_a65b_parity_gate.py`` checks that every in_studio row and every spec check names at least
one, that each named test exists, and that it really clicks. An old page redirects only when every parity row naming it
is home (``in_studio``) or retired, and the owner has not chosen to keep it. Nothing here is guessed: a page with a
``port:`` or ``held:`` row stays a real page.
"""
from __future__ import annotations

from .parity import PARITY_ROWS

P1 = "test_studio_phase1_header.py::"
P2 = "test_studio_phase2_workspace.py::"
GATE = "test_a65b_parity_gate.py::"
PORTS = "test_a65c_playground_ports.py::"

EVIDENCE: dict[str, tuple[str, ...]] = {
    "projects": (P2 + "test_old_projects_filter_files_and_deep_link",),
    "chat-jobs": (GATE + "test_a_chat_job_starts_shows_busy_and_stop_calls_the_cancel_route",),
    "header-controls": (P1 + "test_header_hits_at_laptop_viewports", P1 + "test_effort_preview_posts_and_shows_error"),
    "brainstorm-gdd-start": (P2 + "test_build_stays_on_slug_and_gdd_failure_is_honest",),
    "gates-verify-milestones": (GATE + "test_gates_and_verify_are_clicked_and_report_what_came_back",),
    "guided-build": (P2 + "test_build_progress_and_guided_stream_are_visible", P2 + "test_build_stream_failure_and_cancel_are_visible"),
    "autopilot-core": ("test_a63a_build_controls.py::test_pause_and_resume_follow_the_autopilot",),
    "run-preview": (P2 + "test_preview_git_and_publish_failures",),
    "publish": (P2 + "test_preview_rejects_false_ok_and_distinguishes_publish_copy",
                "test_a0_studio_confirm.py::test_live_publish_asks_before_public_backends"),
    "git-core": ("test_a0_studio_confirm.py::test_git_push_force_only_after_confirm",          # push
                 P2 + "test_preview_git_and_publish_failures",                                # clone
                 GATE + "test_revive_and_pull_request_are_clicked_send_their_bodies_and_a_refusal_stays_an_error"),
    "proposals": (P2 + "test_review_actions_and_write_only_secret",),
    "skills-toggle": (P2 + "test_review_actions_and_write_only_secret",),
    "bundles": (P2 + "test_review_actions_and_write_only_secret",),
    "permissions-basic": (P2 + "test_review_actions_and_write_only_secret",),
    "outbox-basic": (P2 + "test_review_actions_and_write_only_secret",),
    "blockers-basic": ("test_studio_blockers.py::test_review_lists_blockers_and_resolve_posts_the_note",),
    "planner-basic": ("test_studio_plan_discovery.py::test_review_confirms_plan_and_discovery_and_hides_approved_rows",),
    "discoveries-basic": ("test_studio_plan_discovery.py::test_review_confirms_plan_and_discovery_and_hides_approved_rows",),
    "skill-creator": ("test_studio_skill_ref.py::test_skill_and_reference_review_flow",),
    "reference-scout": ("test_studio_skill_ref.py::test_skill_and_reference_review_flow",),
    "settings-connectors": (P1 + "test_settings_persist_across_process_and_browser_restart",
                            P2 + "test_review_actions_and_write_only_secret"),
    "chat-upload-doc": (GATE + "test_a_document_is_attached_with_real_clicks_and_goes_with_the_message",),
    "chat-project": ("test_a64_project_chat.py::test_project_chat_is_off_by_default_and_sends_the_project_only_when_switched_on",
                     "test_a64_project_chat.py::test_the_changes_of_a_project_turn_are_listed_with_diffs_and_a_revert_that_asks"),
    "chat-classic-blocking": (GATE + "test_a_chat_job_starts_shows_busy_and_stop_calls_the_cancel_route",),
    "settings-workers": ("test_a61_settings.py::test_a_setting_is_saved_with_real_clicks_and_survives_a_reload",
                         "test_a61_settings.py::test_a_task_model_is_set_and_cleared_with_real_clicks"),
    "settings-connection-tests": ("test_a61b_connections.py::test_provider_and_git_tests_report_in_plain_words_with_real_clicks",
                                  "test_a61b_connections.py::test_mcp_is_enabled_only_after_a_confirm_and_a_declined_confirm_changes_nothing"),
    "settings-kernel": ("test_a61c_maintenance.py::test_a_valid_kernel_config_is_saved_and_survives_a_reload",),
    "build-autopilot-controls": ("test_a63a_build_controls.py::test_pause_and_resume_follow_the_autopilot",
                                 "test_a63a_build_controls.py::test_creating_a_plan_needs_the_idea_asks_and_says_when_replacing",
                                 "test_a63a_build_controls.py::test_expanding_and_clearing_ask_first"),
    "build-unity": ("test_a63b_unity_dissect.py::test_waivers_are_offered_only_where_the_studio_allows_one_and_ask_first",
                    "test_a63b_unity_dissect.py::test_attach_template_shows_only_for_cloneable_templates_asks_and_reports",
                    "test_studio_ship_init.py::test_unity_chain_names_overwrite_clone_and_stops"),
    "build-dissect-lab": (GATE + "test_dissection_and_lab_are_clicked_and_call_their_routes",),
    "build-full-auto": ("test_a63a_build_controls.py::test_full_auto_asks_runs_on_the_server_and_shows_where_it_stopped",
                        "test_a63a_build_controls.py::test_stopping_full_auto_sends_stop"),
    "git-browse": ("test_a63c_git_appdata_gist.py::test_github_repos_fill_the_remote_field_and_show_branches_without_cloning",),
    "preview-app-data": ("test_a63c_git_appdata_gist.py::test_app_data_lists_views_saves_and_deletes_with_questions_first",),
    "skill-creator-gist": ("test_a63c_git_appdata_gist.py::test_the_gist_appears_only_for_a_generated_skill_and_asks_before_publishing_publicly",),
    "review-permissions-bulk": ("test_a62a_review.py::test_grant_all_asks_with_the_real_count_and_sends_the_chosen_hours",
                                "test_a62a_review.py::test_renew_says_nothing_to_renew_without_asking_and_otherwise_asks_with_the_count"),
    "review-blockers-actions": ("test_a62a_review.py::test_blocker_auto_fix_asks_first_and_sends_only_the_id_and_hours",
                                "test_a62a_review.py::test_remediation_shows_what_it_will_change_and_asks_only_when_the_kind_changes",
                                "test_a62a_review.py::test_auto_fix_all_and_grant_recommended_show_what_they_will_grant"),
    "review-planner-extras": ("test_a62b_planner.py::test_a_manual_plan_is_created_as_a_draft_through_the_real_server",
                              "test_a62b_planner.py::test_override_asks_first_and_names_what_it_does",
                              "test_a62b_planner.py::test_run_now_asks_twice_and_grants_nothing_unless_the_second_answer_is_yes"),
    "review-discoveries-extras": ("test_a62b_planner.py::test_regenerating_a_plan_asks_runs_in_the_background_and_reports_how_it_ended",
                                  "test_a62b_planner.py::test_deleting_a_discovery_asks_first_and_the_list_follows"),
    "review-details": ("test_a62a_review.py::test_proposal_details_open_and_close_inline_and_a_diff_is_never_markup",
                       "test_a62a_review.py::test_bundle_details_list_each_file_with_its_change",
                       "test_a62a_review.py::test_approve_all_pending_asks_with_the_count_and_reports_a_partial_result_honestly"),
    "review-memory": ("test_a62c_memory.py::test_deleting_a_fact_asks_first_and_the_list_follows",
                      "test_a62c_memory.py::test_consolidation_asks_runs_on_the_server_and_shows_how_it_ended",
                      "test_a62c_memory.py::test_resetting_the_navigator_asks_and_saving_a_mode_sends_the_choice"),
    "review-ledger": ("test_a62c_memory.py::test_adding_a_task_sends_the_fields_and_reports_a_missing_title",
                      "test_a62c_memory.py::test_a_session_opens_and_hides_its_turns",
                      "test_a62c_memory.py::test_a_search_shows_links_only_for_web_addresses_and_says_what_was_saved"),
    # A6.5c: what only the Playground page had
    "pg-suggestions": (PORTS + "test_ideas_ask_first_show_what_came_back_and_one_goes_to_the_chat",),
    "pg-game-hints": (PORTS + "test_hints_and_a_description_use_the_idea_and_say_when_the_model_did_not_write_it",),
    "pg-ai-description": (PORTS + "test_hints_and_a_description_use_the_idea_and_say_when_the_model_did_not_write_it",),
    "pg-coding-mode": (PORTS + "test_the_coding_mode_shows_the_saved_one_and_a_change_is_saved_on_the_real_server",),
    "pg-backup-workspace": (PORTS + "test_backup_asks_first_and_tells_done_skipped_and_failed_apart",),
    "pg-published-list": (PORTS + "test_published_apps_are_listed_with_links_and_a_failed_read_is_not_an_empty_list",),
    "pg-expo-tunnel": (PORTS + "test_the_tunnel_is_offered_only_for_expo_asks_first_and_the_qr_loads_only_after_a_second_yes",),
    "pg-learning": (PORTS + "test_learning_is_read_on_request_and_a_failed_read_is_said",),
}

# The nine phase-3 checks of docs/superpowers/specs/2026-11-07-unified-game-studio-design.md. Check 6 names "apply an
# outbox item": that was retired (R2, owner sign-off 2026-11-09), so outbox evidence is list and dismiss.
SPEC_CHECKS: dict[int, tuple[str, ...]] = {
    1: (P2 + "test_old_projects_filter_files_and_deep_link",),
    2: (GATE + "test_a_chat_job_starts_shows_busy_and_stop_calls_the_cancel_route",),
    3: (P2 + "test_build_stays_on_slug_and_gdd_failure_is_honest",),
    4: (P2 + "test_build_progress_and_guided_stream_are_visible", P2 + "test_build_stream_failure_and_cancel_are_visible",
        "test_studio_guided_resume_owner.py::test_guided_error_and_timeout_do_not_resurrect_a_stale_job"),
    5: (P2 + "test_preview_git_and_publish_failures", P2 + "test_preview_rejects_false_ok_and_distinguishes_publish_copy"),
    6: (P2 + "test_review_actions_and_write_only_secret", P2 + "test_review_requires_explicit_ok"),
    7: (P1 + "test_settings_persist_across_process_and_browser_restart", P2 + "test_review_actions_and_write_only_secret"),
    8: (P1 + "test_header_hits_at_laptop_viewports",),
    9: (P2 + "test_language_keeps_user_text_and_screenshots", P1 + "test_settings_persist_across_process_and_browser_restart"),
}

# Old HTML page -> (parity legacy key, redirect target). The owner keeps the classic page (A6.5 sign-off, 2026-11-09:
# "Redirect others, keep classic"): it also still holds radar/journal (MR126) and auto-apply (R6 kept).
PAGES: dict[str, tuple[str, str]] = {
    "/admin/": ("hub", "/"),
    "/admin/daedalus/hub": ("hub", "/"),
    "/admin/daedalus/playground": ("playground", "/?panel=build"),
    "/admin/daedalus/game-studio": ("game-studio", "/?panel=build"),
    "/admin/daedalus/connectors": ("connectors", "/?panel=settings"),
    "/admin/daedalus/proposals": ("proposals", "/?panel=review&tab=proposals"),
    "/admin/daedalus/skills": ("skills", "/?panel=review&tab=skills"),
    "/admin/daedalus/bundles": ("bundles", "/?panel=review&tab=bundles"),
    "/admin/llm-router": ("llm-router", "/?panel=settings"),
}
KEPT = {"classic"}


def blocking_rows(legacy_key: str) -> list[str]:
    """Rows that keep an old page alive: anything naming it that is neither home nor retired."""
    return [r.id for r in PARITY_ROWS if legacy_key in r.legacy and not (r.disposition == "in_studio" or r.disposition.startswith("retired:"))]


def redirect_target(path: str) -> str | None:
    """Where an old page now goes, or None while it must stay a real page."""
    entry = PAGES.get(path)
    if entry is None:
        return None
    key, target = entry
    if key in KEPT or blocking_rows(key):
        return None
    return target


def summary() -> dict:
    return {
        "redirected": {p: redirect_target(p) for p in PAGES if redirect_target(p)},
        "kept": {p: blocking_rows(k) for p, (k, _) in PAGES.items() if not redirect_target(p)},
        "unchecked_rows": sorted(r.id for r in PARITY_ROWS if r.disposition == "in_studio" and r.id not in EVIDENCE),
    }
