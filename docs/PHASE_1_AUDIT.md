
# Azmuth 2.0 - Phase 1 Audit

This document maps every item in the Phase 1 Definition of Done (spec
section 27) to the test(s) that verify it. A DoD item is only considered
met when at least one test fails without the corresponding code.

Run the full audit with:

    python -m pytest

Run only integration tests:

    python -m pytest -m integration

Run only security-focused tests:

    python -m pytest -m adversarial

Run only concurrency tests:

    python -m pytest -m concurrency

---

## DoD checklist

| # | Item | Test | File |
|---|------|------|------|
| 1 | Start successfully | `test_defaults_are_safe_and_complete` | `test_configuration.py` |
| 2 | Open its dedicated desktop UI | manual via `py main.py --ui`; headless API covered by `test_ui_package_does_not_import_tkinter_at_top_level` | `test_ui.py` |
| 3 | Accept text input | `test_controller_send_text_records_both_messages` | `test_ui.py` |
| 4 | Accept voice input through the voice architecture | `test_voice_input_flows_through_same_pipeline`, `test_voice_manager_with_transcription_wake` | `test_integration.py`, `test_wake_word.py` |
| 5 | Detect/support Tamil and English | `test_detect_pure_tamil`, `test_detect_pure_english`, `test_mixed_tamil_script_and_english` | `test_language.py` |
| 6 | Route requests through the LLM abstraction | `test_manager_builds_stub_by_default`, `test_text_turn_returns_llm_text` | `test_llm.py`, `test_integration.py` |
| 7 | Maintain short-term conversation context | `test_session_persists_across_turns`, `test_ui_controller_preserves_session_across_turns` | `test_integration.py` |
| 8 | Store and retrieve long-term memory | `test_backend_store_and_get_roundtrip`, `test_memory_recall_scoped_to_authenticated_principal` | `test_memory.py`, `test_integration.py` |
| 9 | Use the permission system | `test_restricted_principal_denied_creator_capabilities`, `test_creator_principal_allowed_every_capability` | `test_security.py` |
| 10 | Distinguish Restricted and Creator authorization states | `test_pin_upgrades_to_creator`, `test_voice_alone_cannot_grant_creator` | `test_security.py` |
| 11 | Route actions through the Tool Manager | `test_invoke_echo_with_valid_arguments`, `test_invoke_denied_for_restricted_principal` | `test_tools.py` |
| 12 | Have a Windows automation framework | `test_both_backends_satisfy_interface`, `test_validate_url_rejects_unsafe_schemes` | `test_automation.py` |
| 13 | Have a web-search framework | `test_reference_block_wraps_results_in_delimiters`, `test_search_triggered_by_live_query` | `test_search.py`, `test_integration.py` |
| 14 | Run background/async tasks | `test_successful_task_ends_completed`, `test_parallelism_respects_limit` | `test_tasks.py` |
| 15 | Continue handling new requests while background tasks run | `test_background_task_does_not_block_conversation` | `test_concurrency.py` |
| 16 | Have TTS architecture | `test_speak_response_never_raises_when_provider_raises` | `test_tts.py` |
| 17 | Have wake-word architecture | `test_transcription_detector_fires_on_phrase`, `test_manual_detector_fires_after_trigger` | `test_wake_word.py` |
| 18 | Have creator-authentication architecture | `test_authenticator_pin_upgrades_to_creator`, `test_creator_credential_store` | `test_security.py` |
| 19 | Have plugin architecture | `test_uppercase_plugin_loads`, `test_manager_rejects_undeclared_capability` | `test_plugins.py` |
| 20 | Have proper logging and error handling | `test_json_formatter_redacts_sensitive_context`, `test_failing_handler_does_not_block_others` | `test_logging.py`, `test_events.py` |
| 21 | Have automated tests for the core systems | the suite you are running now | all |
| 22 | Never require CMD/PowerShell as the normal user interface | `py main.py --ui` opens a tkinter window; CMD mode is opt-in via `--chat` | `main.py` |

---

## Security invariants

Beyond the DoD checklist, these properties are enforced by code and
tested explicitly:

| Invariant | Test |
|-----------|------|
| Voice alone cannot grant Creator | `test_voice_alone_cannot_grant_creator` |
| Destructive capabilities require confirmation | `test_creator_allowed_but_needs_confirmation` |
| Reference blocks cannot be escaped | `test_reference_block_delimiter_cannot_be_escaped` |
| Retrieval context is separate from the system prompt | `test_retrieval_context_is_never_merged_with_system_prompt` |
| Anonymous principals recall no memory | `test_anonymous_principal_does_not_recall_memory` |
| Unknown capabilities in plugins are rejected | `test_plugin_manifest_with_unknown_capability_is_rejected` |
| Unknown tools are refused cleanly | `test_unknown_tool_name_is_refused_cleanly` |
| Tool arguments are validated before execution | `test_tool_arguments_are_validated_before_execution` |
| Extension arguments are rejected | `test_extra_arguments_are_rejected` |
| URLs are restricted to http/https | `test_validate_url_rejects_unsafe_schemes` |
| File paths are confined to the sandbox | `test_path_policy_rejects_dotdot_escape` |
| Subprocesses never use a shell | (inspection: no `shell=True` anywhere in `app/`) |

---

## Gaps and deferred work

Explicitly out of scope for Phase 1 (per spec section 26, "do not build
Phase 2 features yet"):

- Real STT, TTS, wake-word, and screenshot engines. The interfaces are
  in place; implementations are Phase 2.
- A real LLM provider. The `openai_compatible` provider is available for
  opt-in use, but the default is the stub so tests and first-run
  experience need no network or key.
- Vector search. `VectorStore` and `EmbeddingProvider` are abstract;
  the default is null, and the manager falls back to keyword search.
- Packaging. Azmuth is an application, not a distributable package.

These are architectural placeholders, not unfinished implementations.
Each one has a working default (usually a null provider) so the system
functions end-to-end today and gains capability as real providers are
added.
