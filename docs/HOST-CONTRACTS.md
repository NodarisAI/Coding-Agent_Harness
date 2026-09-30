# Hook contracts: Codex CLI, Gemini CLI, Cursor, OpenCode, Claude Code (reference)

Date: 2026-09-26. Purpose: an exact, per-host hook contract so one harness engine can generate each host's native hook config from one policy source. Every fact below carries a `file:line` citation into a local source clone, an installed package's shipped type declarations, or a docs URL. Claims not directly confirmed in this pass are marked `[unverified]`. Versions installed on this machine: Codex CLI 0.137.0, Gemini CLI 0.45.2, OpenCode 1.17.3 (its `@opencode-ai/plugin` dependency reports 1.15.13 — a version lag `[unverified — cause]`), Cursor via `cursor-agent` (closed source, docs-only).

Builds on and corrects `second-brain/raw/2026-09-25-harness-research/15-cross-agent-config-matrix.md`, which already resolved several earlier misreadings (Gemini hooks live in `settings.json`, not `.gemini/hooks/`; OpenCode's permission key is singular `permission`, not `agents.<id>.permissions`). This document goes one level deeper: exact wire schemas, not just "supported/not supported."

---

## 1. Codex CLI

Codex's Rust core (`codex-rs/`) has the richest, most explicitly typed hook contract of any host checked — it ships JSON Schema fixtures for every event's input and output.

### Config file locations and structure

Two equivalent representations, both loaded per config layer (managed/admin → user → project, `codex-rs/hooks/src/engine/discovery.rs:118-187`):

1. **`[hooks]` table inside `config.toml`** (`~/.codex/config.toml` for user scope; a project-level `.codex/config.toml` layer is read by the same `ConfigLayerStack` mechanism, but this pass did not re-confirm the exact project file path string `[unverified]`). Struct: `codex-rs/config/src/hook_config.rs:10-25` (`HooksFile`/`HooksToml` wrap `HookEventsToml`).
2. **A standalone `hooks.json`** at `<config_folder>/hooks.json` — i.e. `~/.codex/hooks.json` or the project `.codex/hooks.json` — loaded by `load_hooks_json` (`codex-rs/hooks/src/engine/discovery.rs:146-148,339-343`). If both a TOML `[hooks]` table and a `hooks.json` exist in the same layer, Codex loads both and emits a warning to prefer one (`discovery.rs:154-164`).

`HookEventsToml` (`hook_config.rs:35-61`) has one field per event, each `Vec<MatcherGroup>`; `MatcherGroup` (`hook_config.rs:153-159`) is `{ matcher: Option<String>, hooks: Vec<HookHandlerConfig> }`; `HookHandlerConfig` (`hook_config.rs:161-201`) is a tagged enum on `type`: `command` (`command`, optional `commandWindows`, `timeout` seconds, `async` bool, `statusMessage`, `additionalContextLimit`), `mcp_tool` (`server`, `tool`, `input`), `prompt`, or `agent`.

**Minimal working example — `config.toml`:**
```toml
[[hooks.PreToolUse]]
matcher = "Bash"

  [[hooks.PreToolUse.hooks]]
  type = "command"
  command = "/absolute/path/to/block-rm.sh"
  timeout = 10
```

**Equivalent `hooks.json`** (same struct, JSON-serialized per `hook_config.rs:10-17`):
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "/absolute/path/to/block-rm.sh", "timeout": 10 }
        ]
      }
    ]
  }
}
```

An admin-only `allow_managed_hooks_only = true` in `requirements.toml` makes only managed-tier hooks run, ignoring user/project hook config entirely (`docs/config.md:9-15`, enforced in `discovery.rs:107-116,143`). Codex fingerprints each hook's command text (`trusted_hash` in `hook_config.rs:27-32`); an edited hook is re-prompted for trust before it runs.

### Event names (`HookEventsToml`, `hook_config.rs:37-60`; 12 total)

`PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`.

Mapped to the task's required triggers: before tool/shell → `PreToolUse`; after tool success → `PostToolUse`; after tool failure → **no dedicated `PostToolUseFailure` event** — Codex's `PostToolUse` fires once per call and its stdin does not encode a distinct failure state in the fields read this pass `[unverified — whether tool_response carries an error field on failure]`; user prompt submitted → `UserPromptSubmit`; agent about to stop → `Stop` (and `SubagentStop` for a subagent); before compaction → `PreCompact`; session start → `SessionStart`; file read → **no distinct event** — reads go through the same `PreToolUse`/`PostToolUse` with `tool_name` set to whatever tool performed the read (Codex's `Bash` covers shell-executed reads; no dedicated read-tool hook name was found in `core/src/tools/hook_names.rs` in this pass `[unverified — exhaustive tool list not enumerated]`); file edit → `PreToolUse`/`PostToolUse` with `tool_name: "apply_patch"` (see below).

### Tool-name values on stdin (`core/src/tools/hook_names.rs:14-67`)

`HookToolName` carries one **canonical name serialized to stdin** plus optional **matcher-only aliases** (aliases never appear in the payload, only in matcher config):
- Shell: canonical `"Bash"` (`hook_names.rs:54-56`).
- File write/edit: canonical `"apply_patch"`, with `"Write"` and `"Edit"` accepted as **matcher aliases only** for Claude Code-style hook configs — the JSON payload's `tool_name` is always `"apply_patch"`, never `"Write"`/`"Edit"` (`hook_names.rs:28-39`).
- Subagent spawn: canonical `"spawn_agent"`, matcher alias `"Agent"` (`hook_names.rs:41-51`).

### Exact stdin payload fields (generated JSON Schema fixtures, `codex-rs/hooks/schema/generated/*.json`)

**`pre-tool-use.command.input.schema.json`** — required: `cwd`, `hook_event_name` (const `"PreToolUse"`), `model`, `permission_mode` (`default|acceptEdits|plan|dontAsk|bypassPermissions`), `session_id`, `tool_input`, `tool_name`, `tool_use_id`, `transcript_path` (nullable string), `turn_id`; optional `agent_id`/`agent_type` when inside a subagent. The command string for a shell call lives at `tool_input.command`; file path for `apply_patch` lives inside `tool_input` per the patch payload shape (not independently confirmed field-by-field this pass `[unverified]`).

**`post-tool-use.command.input.schema.json`** — same required set plus `tool_response` (the tool's raw result), i.e. `tool_response` replaces nothing, it's additive to `tool_input`.

**`stop.command.input.schema.json`** — required: `cwd`, `hook_event_name` (const `"Stop"`), `last_assistant_message` (nullable), `model`, `permission_mode`, `session_id`, `stop_hook_active` (bool — true when this Stop hook run is itself the result of a prior Stop block, preventing infinite loops), `transcript_path`, `turn_id`.

**`session-start.command.input.schema.json`** — required: `cwd`, `hook_event_name` (const `"SessionStart"`), `model`, `permission_mode`, `session_id`, `source` (`startup|resume|clear|compact|fork`), `transcript_path`.

Session id lives at top-level `session_id` on every event; cwd at top-level `cwd`; both are strings, absolute-path for `cwd` (`AbsolutePathBuf` in `hook_runtime.rs:65-66,155-163,196-208`).

### Output contract

Parsed from **stdout JSON only** (`hooks/src/engine/output_parser.rs:93-357`); this pass did not confirm whether a non-zero/exit-2 process exit code independently forces a block the way Claude Code and Gemini CLI do — `command_runner.rs` captures `exit_code` (`command_runner.rs:247,280,295,307,318,321,363,380`) but tracing how it feeds `HookRunStatus::Blocked` vs. the JSON `should_block` path was not completed this pass `[unverified]`.

- **Deny (PreToolUse)**: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<non-empty>"}}` — the reason is required non-empty or the whole output is rejected as invalid (`output_parser.rs:452-472`, `schema.rs:244-265`). A legacy shape `{"decision": "block", "reason": "..."}` is also accepted (`output_parser.rs:154-158`, `schema.rs:267-271`) but `"decision": "approve"` is explicitly **unsupported** (`output_parser.rs:489-491`) — Codex only understands allow-by-default/deny, not an explicit approve signal.
- **Allow + rewrite input**: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {...}}}` — `updatedInput` is only honored paired with `permissionDecision: "allow"` (`output_parser.rs:162-173,444-457`).
- **`permissionDecision: "ask"` is explicitly rejected as unsupported** (`output_parser.rs:458-460`) — Codex has no PreToolUse-level "ask the user" hook outcome; that only happens through the separate `PermissionRequest` event.
- **Add context**: `hookSpecificOutput.additionalContext` on `PreToolUse`, `PostToolUse` (`additionalContext` field, `output_parser.rs:227-229`), and `SessionStart`/`SubagentStart` (`output_parser.rs:93-119`).
- **Block on PostToolUse**: top-level `{"decision": "block", "reason": "<non-empty>"}` (`output_parser.rs:207-239`); `updatedMCPToolOutput` is reserved and currently rejected as unsupported (`output_parser.rs:431-436`).
- **Stop-equivalent (block the agent from stopping)**: yes — `Stop` and `SubagentStop` accept `{"decision": "block", "reason": "<non-empty>"}` (`output_parser.rs:290-332`); an empty/missing reason on a block decision invalidates the whole hook output (`invalid_block_message`, `output_parser.rs:365-367`).
- **Universal fields** on every output: `continue` (bool, default true), `stopReason`, `suppressOutput`, `systemMessage` (`HookUniversalOutputWire`, `schema.rs:87-99`) — but `PreToolUse` and `PermissionRequest` explicitly reject `continue:false`, a set `stopReason`, or `suppressOutput:true` as unsupported for those two events (`output_parser.rs:369-391`).
- **Timeouts**: per-hook `timeout` in **seconds** in the TOML/JSON config (`hook_config.rs:169-170`), no universal default surfaced in this pass beyond what's in `command_runner.rs` (`Duration::from_secs(handler.timeout_sec)`, `command_runner.rs:280`) `[unverified default value]`.

### Rules and skills

- Instructions: `AGENTS.md`, discovered/merged like Claude Code merges `CLAUDE.md` (`codex-rs/core/src/agents_md.rs`, tested in `agents_md_tests.rs` — per matrix doc line 13, re-confirmed by file presence, not re-read this pass).
- Skills: native `SKILL.md` under `.agents/skills/<name>/` (`codex-rs/core/tests/suite/codex_delegate.rs:281-290`, per matrix doc's verification pass).

---

## 2. Gemini CLI

Gemini CLI's hook contract is fully documented in-repo (`docs/hooks/index.md`, `docs/hooks/reference.md`) with an explicit "Golden Rule" around stdout hygiene that neither Codex nor Claude Code state as bluntly.

### Config file locations and structure

`settings.json`, merged **highest to lowest precedence**: project `.gemini/settings.json` → user `~/.gemini/settings.json` → system `/etc/gemini-cli/settings.json` → installed extensions (`docs/hooks/index.md:94-100`). A workspace-level `.gemini/policies/*.toml` (the separate permission-policy engine, not hooks) is currently **non-functional** — "Defining policies in a workspace's `.gemini/policies` directory will not have any effect" per the matrix doc's citation of `docs/reference/policy-engine.md:127-131`; that does not affect hooks, only the allow/deny/ask_user policy layer.

**Minimal working example:**
```json
{
  "hooks": {
    "BeforeTool": [
      {
        "matcher": "write_file|replace",
        "hooks": [
          {
            "name": "security-check",
            "type": "command",
            "command": "$GEMINI_PROJECT_DIR/.gemini/hooks/security.sh",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```
(`docs/hooks/index.md:104-122`.) Hook-definition fields: `type` (only `"command"` currently), `command`, `name`, `timeout` (ms, default 60000), `description` (`docs/hooks/index.md:126-132`, `docs/hooks/reference.md:36-42`). The array wrapper adds an optional `sequential: boolean` (parallel by default) (`docs/hooks/reference.md:31`).

### Event names (`docs/hooks/index.md:38-51`, `docs/hooks/reference.md`)

`SessionStart`, `SessionEnd`, `BeforeAgent`, `AfterAgent`, `BeforeModel`, `AfterModel`, `BeforeToolSelection`, `BeforeTool`, `AfterTool`, `PreCompress`, `Notification`.

Mapped: before tool/shell → `BeforeTool` matched on `tool_name` (shell tool documented as `run_shell_command`, `docs/hooks/reference.md:84-85`); after tool success/failure → `AfterTool` (one event; success/failure is inside `tool_response.error`, not a separate event, per the `tool_response` shape documented at `reference.md:122-123` — `[unverified whether error is a distinct sub-field vs. inferred from absence of llmContent]`); user prompt submitted → `BeforeAgent` ("After user submits prompt, before planning" — this is Gemini's `UserPromptSubmit` equivalent); agent about to stop → **no literal `Stop` event** — the functional equivalent is `AfterAgent` (`decision: "deny"` rejects the response and forces a retry; `continue: false` stops the session, `docs/hooks/reference.md:163-181`); before compaction → `PreCompress`; session start → `SessionStart`; file read → `BeforeTool`/`AfterTool` matched on `tool_name: "read_file"` (documented example, `reference.md:84-90`); file edit → `BeforeTool`/`AfterTool` matched on `tool_name: "write_file"` or `"replace"` (config example, `index.md:109`).

### Stdin payload fields

**Base fields on every event** (`docs/hooks/reference.md:50-57`): `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `timestamp` (ISO 8601).

**`BeforeTool`** adds: `tool_name`, `tool_input` (object — the shell command string lives at `tool_input.command` for `run_shell_command`, the file path at `tool_input.file_path`/`path` for `read_file`/`write_file`, exact key not independently re-verified this pass `[unverified exact key name]`), `mcp_context`, `original_request_name` (`reference.md:97-102`).

**`AfterTool`** adds: `tool_name`, `tool_input`, `tool_response` (`{llmContent, returnDisplay, error?}`), `mcp_context`, `original_request_name` (`reference.md:119-126`).

MCP tool names follow `mcp_<server_name>_<tool_name>` (`reference.md:87-88`).

### Output contract

- **Exit codes** (`docs/hooks/reference.md:10-15`, `index.md:70-79`): `0` = success, stdout parsed as JSON (preferred for everything, including intentional blocks); `2` = System Block — the action is aborted and **`stderr`** (not stdout) is used as the rejection/reason text; any other code = non-fatal warning, original parameters used.
- **"Golden Rule"**: any non-JSON text on stdout — even one stray `echo` — breaks parsing; the CLI then defaults to Allow and treats the whole stdout blob as a `systemMessage` (`docs/hooks/index.md:60-68`).
- **Deny/allow**: top-level `decision`: `"allow"` or `"deny"` (alias `"block"`) (`reference.md:72`); `reason` is the text shown when denied (`reference.md:73`). For `BeforeTool`, `reason` is required on deny and is sent **to the agent** as a tool error so it can retry (`reference.md:104-107`).
- **Add context**: `hookSpecificOutput.additionalContext` — for `BeforeAgent` it's appended to the prompt for that turn only; for `AfterTool` it's appended to the tool result (`reference.md:131-132,153-154`).
- **Rewrite tool args**: `hookSpecificOutput.tool_input` on `BeforeTool` **merges with and overrides** the model's arguments (`reference.md:108-109`).
- **Stop-equivalent**: `AfterAgent`'s `decision: "deny"` rejects the response and forces an automatic retry, sending `reason` to the agent as a new corrective prompt; `continue: false` stops the session outright without retrying (`reference.md:173-181`). Exit code 2 on `AfterAgent` triggers the same retry path using `stderr` as the feedback (`reference.md:180-181`).
- **Kill the whole loop from any tool/model hook**: `continue: false` (`reference.md:70-71,110,137,235`).
- **Common output fields** available broadly: `systemMessage`, `suppressOutput`, `continue`, `stopReason`, `decision`, `reason` (`reference.md:62-73`).
- **Timeout**: per-hook `timeout` in **milliseconds**, default 60000 (`reference.md:41`, `index.md:131`).

### Rules and skills

- Instructions: `GEMINI.md`, three-tier hierarchy — global `~/.gemini/GEMINI.md`, workspace-searched `GEMINI.md` files, and just-in-time per-directory `GEMINI.md` files (`docs/cli/gemini-md.md:1-35`). `AGENTS.md` is **not** read by default; it requires setting `context.fileName` in `settings.json` to a list that includes it, e.g. `["AGENTS.md", "CONTEXT.md", "GEMINI.md"]` (`docs/cli/gemini-md.md:44-52`) — confirming the matrix doc's correction #5.
- Skills: `SKILL.md`-based, Agent Skills open standard. Discovery tiers lowest→highest precedence: built-in skills, extension-bundled skills, then **user skills at `~/.gemini/skills/` or the alias `~/.agents/skills/`** (`docs/cli/skills.md:1-45`); project-level skill discovery tier exists but wasn't reached in the excerpt read this pass `[unverified — likely `.gemini/skills/`, not directly confirmed]`.

---

## 3. Cursor (closed source — docs only, `cursor.com/docs/hooks`, fetched 2026-09-26)

No local source clone exists for Cursor; every fact below is from the current published docs page via a single fetch pass. Treat field-name precision here as lower-confidence than Codex/Gemini, which came from source and generated schema fixtures.

### Config file locations and structure

- User-level (global): `~/.cursor/hooks.json`.
- Project-level: `<project-root>/.cursor/hooks.json`.
- Enterprise/system-wide (admin-only, cannot be overridden by project or user config): macOS `/Library/Application Support/Cursor/hooks.json`, Linux/WSL `/etc/cursor/hooks.json`, Windows `C:\ProgramData\Cursor\hooks.json`.
- Cursor **also loads hooks written for Claude Code**: "Cursor supports loading hooks from third-party tools like Claude Code," and its exit-code behavior "matches Claude Code behavior for compatibility" — per the background matrix doc's citation of the same docs page; the exact file paths it reads for that compatibility mode were not resolved in either pass `[unverified]`.

**Minimal working example:**
```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      { "command": "./hooks/script.sh", "timeout": 30, "matcher": "curl|wget" }
    ]
  }
}
```
Project hooks run with cwd at the project root (script paths like `.cursor/hooks/script.sh`); user hooks run from `~/.cursor/`.

### Event names

Agent hooks: `sessionStart`/`sessionEnd`, `preToolUse`/`postToolUse`/`postToolUseFailure`, `subagentStart`/`subagentStop`, `beforeShellExecution`/`afterShellExecution`, `beforeMCPExecution`/`afterMCPExecution`, `beforeReadFile`/`afterFileEdit`, `beforeSubmitPrompt`, `preCompact`, `stop`, `afterAgentResponse`/`afterAgentThought`. Tab-specific: `beforeTabFileRead`/`afterTabFileEdit`. App lifecycle: `workspaceOpen`.

Mapped: before tool/shell → `beforeShellExecution` (shell specifically) or generic `preToolUse`; after tool success → `postToolUse`; after tool failure → **`postToolUseFailure` exists as its own event**, unlike Codex and Gemini which fold failure into the same post-event; user prompt submitted → `beforeSubmitPrompt`; agent about to stop → `stop`; before compaction → `preCompact`; session start → `sessionStart`; file read → `beforeReadFile` (and `beforeTabFileRead` for tab-driven reads); file edit → `afterFileEdit` (no separate "before edit" event was listed — editing appears to be audited only after the fact via `afterFileEdit`, unlike Codex/Gemini which gate the edit beforehand through their generic pre-tool event `[unverified — a beforeFileEdit/beforeWrite event may exist but wasn't listed on the fetched page]`).

### Stdin payload fields

Common base fields on every hook: `conversation_id`, `generation_id`, `model`, `model_id`, `hook_event_name`, `cursor_version`, `workspace_roots`, `user_email`, `transcript_path`. No explicit `session_id` field name was returned — `conversation_id` appears to be the session identifier `[unverified — may be named differently for CLI vs IDE]`.

- `beforeShellExecution`: `{command, cwd, sandbox}` — the command string is at the **root-level `command` field**.
- `beforeReadFile`: `{file_path, content, attachments}` — file path at **root-level `file_path`**.
- `afterFileEdit`: `{file_path, edits: [{old_string, new_string}]}`.
- `beforeSubmitPrompt`: `{prompt, attachments}`.
- `beforeMCPExecution`: `{tool_name, tool_input, mcp_server_name, url, command}`.

### Output contract

- Exit 0: JSON output used (invalid JSON blocks the action for permission-class hooks). Exit 2: blocks the action (equivalent to `"deny"`). Other codes: fail-open by default; set `"failClosed": true` on the hook config to block on any non-zero exit instead.
- **Permission-class hooks** (`beforeShellExecution`, `beforeReadFile`, `beforeMCPExecution`, etc.) return `{"permission": "allow" | "deny" | "ask", "user_message": "...", "agent_message": "..."}` — note Cursor has a genuine three-way `"ask"` outcome here, which Codex explicitly does **not** support at the PreToolUse-equivalent layer.
- **`afterFileEdit`/`postToolUse`**: `{"additional_context": "...", "updated_mcp_tool_output": {...}}` for adding context back to the model.
- **`stop`/`subagentStop`**: `{"followup_message": "..."}` — auto-submitted as the next user message, which is Cursor's way of keeping the agent going rather than a `decision:block`-style refusal to stop.
- **Timeout**: `timeout` field in **seconds** per hook config, default is platform-dependent and not stated exactly on the fetched page `[unverified exact default]`.

### Rules and skills

- Rules: project-level `.cursor/rules/*.mdc` files (frontmatter-bearing, version-controlled); user-level rules are set through **Settings → Rules** in the app UI, not a documented on-disk path. Cursor also natively reads `AGENTS.md` in the project root and subdirectories as a plain-markdown alternative to `.mdc` rules, with nested files taking precedence over parent ones (`cursor.com/docs/context/rules`, fetched 2026-09-26).
- Skills: native `SKILL.md` via the agentskills.io open standard; Cursor is named as a first-tier adopter alongside Claude Code and Codex (per matrix doc, `cursor.com/docs/skills`, not independently re-fetched this pass).
- Plugins: "Agent Plugins" (per matrix doc) bundle rules, skills, agents, commands, MCP servers, and hooks into one distributable unit for both the Cursor IDE and CLI — the most direct install target for a cross-host harness package on Cursor.

---

## 4. OpenCode

Unlike the others, OpenCode's hook surface is a **JavaScript/TypeScript plugin API**, not a declarative config file — confirmed directly against the installed `@opencode-ai/plugin` package on this machine (`/Users/Codesmart-Project/.opencode/node_modules/@opencode-ai/plugin/dist/index.d.ts`, package version 1.15.13, CLI 1.17.3), not just docs.

### Config file locations and structure

- Config file: `opencode.json` or `opencode.jsonc`. User-level: `~/.config/opencode/opencode.json` (also `tui.json` alongside it). Project-level: `opencode.json` in the project root, plus a `.opencode/` directory holding agents, commands, plugins, and skills. Overridable via `OPENCODE_CONFIG` / `OPENCODE_CONFIG_DIR` env vars. System-managed (admin): `/Library/Application Support/opencode/` (macOS), `/etc/opencode/` (Linux), `%ProgramData%\opencode` (Windows). (`opencode.ai/docs/config`, fetched 2026-09-26.)
- **Hooks themselves are not declared in `opencode.json`.** They are exported from a plugin module (a `.ts`/`.js` file under `.opencode/plugin/` or referenced via the `plugin` config array `Array<string | [string, PluginOptions]>`) that returns a `Hooks` object (`index.d.ts:36-51,173`).

**Minimal working example** — `.opencode/plugin/block-rm.ts`:
```ts
import type { Plugin } from "@opencode-ai/plugin";

export const BlockRm: Plugin = async ({ $ }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool === "bash" && String(output.args?.command ?? "").includes("rm -rf")) {
        throw new Error("Destructive command blocked by hook");
      }
    },
  };
};
```
Registered in `opencode.json` via `"plugin": ["./.opencode/plugin/block-rm.ts"]` (shape per `index.d.ts:47-51`; the plugin-array registration path itself is inferred from the `Config` type, not independently confirmed against a worked example `[unverified exact registration syntax]`).

### Event names (`Hooks` interface, `index.d.ts:173-317` — this is the complete, exhaustive list as shipped, not a docs paraphrase)

`dispose`, `event`, `config`, `tool` (custom tool definitions), `auth`, `provider`, `chat.message`, `chat.params`, `chat.headers`, `permission.ask`, `command.execute.before`, `tool.execute.before`, `shell.env`, `tool.execute.after`, `experimental.chat.messages.transform`, `experimental.chat.system.transform`, `experimental.session.compacting`, `experimental.compaction.autocontinue`, `experimental.text.complete`, `tool.definition`.

Mapped to the task's required triggers — and this mapping surfaces real gaps, not just naming differences: before tool/shell → `tool.execute.before` (fires for every tool including `bash`, `read`, `edit`; there is **no shell-specific event**, only the generic tool hook filtered on `input.tool === "bash"`); after tool success → `tool.execute.after`; after tool failure → **no distinct event found in the shipped types** — `tool.execute.after`'s `output` shape (`{title, output, metadata}`) carries no explicit error/success discriminant in this interface `[unverified — failure signaling may be encoded inside `output.metadata` or `output.output` text, not typed separately]`; user prompt submitted → **no literal `UserPromptSubmit`-equivalent event exists in this interface** — the closest is `chat.message` ("Called when a new message is received," `index.d.ts:184-199`), which is a notification hook (no block/deny return path in its type signature) rather than a gate; agent about to stop → **no `Stop`/`SubagentStop` event exists in this interface at all** `[verified absence — not in the 19-key list above]`; before compaction → `experimental.session.compacting` (can replace or extend the compaction prompt, `index.d.ts:271-283`) and `experimental.compaction.autocontinue` (can suppress the post-compaction synthetic continue turn, `index.d.ts:284-300`); session start → **no `SessionStart` event exists in this interface** `[verified absence]`; file read → `tool.execute.before`/`.after` filtered on `input.tool === "read"` (tool name inferred from OpenCode's built-in tool naming convention, not independently re-verified against a tool-name enum in this pass `[unverified exact string]`); file edit → same, filtered on `input.tool === "edit"` or `"write"` `[unverified exact string]`.

This is the single most consequential finding for a cross-host harness: **OpenCode's plugin API has no session-start, no user-prompt-gate, and no stop-gate hook.** A harness that assumes those three exist everywhere (true for Codex, Gemini, Cursor, and Claude Code) will silently no-op on OpenCode for those three policies.

### Stdin payload fields — not applicable as "stdin"; these are typed JS function arguments

- `tool.execute.before(input: {tool: string, sessionID: string, callID: string}, output: {args: any})` (`index.d.ts:235-241`) — the tool name is `input.tool`; session id is `input.sessionID`; the command/file-path/etc. lives inside the **mutable** `output.args` object (its shape is tool-specific and untyped — `any`), not `input`.
- `tool.execute.after(input: {tool, sessionID, callID, args}, output: {title, output, metadata})` (`index.d.ts:249-258`) — note `args` moves into `input` here (the call's original arguments), and the result is in `output.output`/`output.title`.
- `shell.env(input: {cwd, sessionID?, callID?}, output: {env})` (`index.d.ts:242-248`) is the only hook carrying an explicit `cwd` field in this interface.
- `permission.ask(input: Permission, output: {status: "ask"|"deny"|"allow"})` (`index.d.ts:225-227`) — `Permission` type not expanded in this pass `[unverified fields]`.
- No field in this interface is named `session_id` (snake_case) anywhere — OpenCode is consistently `sessionID` (camelCase), unlike every other host's snake_case stdin convention.

### Output contract

**There is no separate "output contract" — the hook function's return/throw behavior *is* the contract**, since this is an in-process JS callback, not a subprocess with stdout/exit codes.

- **Deny/block**: `tool.execute.before` blocks the tool call by **throwing an `Error`** inside the async function (confirmed by the matrix doc's citation of `opencode.ai/docs/plugins/`: "`tool.execute.before` exists and can block by throwing an error" — settling that doc's earlier `[unverified]`). There is no boolean/decision field in the typed signature; the mechanism is exception-based.
- **Allow**: return normally (resolve the promise) without throwing.
- **Rewrite args**: mutate the `output.args` object in place before returning (`index.d.ts:239-241`) — same in-place-mutation pattern as `tool.execute.after`'s `output.output`/`output.metadata` and `shell.env`'s `output.env`.
- **Add context for the model**: no dedicated `additionalContext`-style field was found in this interface; `experimental.chat.system.transform` (`output: {system: string[]}`) and `experimental.chat.messages.transform` are the closest mechanisms, operating on the whole system prompt / message list rather than injecting a scoped note at the hook's firing point `[unverified — no direct analogue to Claude/Codex/Gemini's per-event additionalContext]`.
- **Stop-equivalent**: **does not exist** — see above.
- **Timeout**: no `timeout` field anywhere in this interface; a hook that hangs presumably hangs the whole tool call, since it's `await`ed in-process `[unverified — no evidence of an enforced timeout]`.

### Permissions (separate from hooks — the actual enforceable gate for OpenCode)

Per the background matrix doc's corrected finding (source: `opencode.ai/docs/permissions/`, verified against the OpenCode `permission` key, singular): top-level `permission` and per-agent `agent.<name>.permission` accept `allow`/`ask`/`deny` (or a glob/pattern object) for `read`, `edit`, `glob`, `grep`, `bash`, `task`, `skill`, `lsp`, `question`, `webfetch`, `websearch`, `external_directory`, `doom_loop`. Most default to `allow`; `external_directory` and `doom_loop` default to `ask`; `*.env` reads default to `deny`. This — not the plugin hook API — is OpenCode's real model-proof enforcement layer, and it's config, not code, so it's a better harness target for "deny bash by default" than trying to replicate `PreToolUse` blocking through a thrown exception.

### Rules and skills

- Rules: `AGENTS.md` at the project root, read natively (`opencode.ai/docs/rules/`). Falls back to `CLAUDE.md`/`~/.claude/CLAUDE.md` when no `AGENTS.md` exists; if both exist, only `AGENTS.md` is used. `OPENCODE_DISABLE_CLAUDE_CODE=1` disables the fallback (matrix doc's verified finding).
- Skills: `SKILL.md` under `.claude/skills/`, `~/.claude/skills/`, and `.agents/skills/` are read (matrix doc, `opencode.ai/docs/skills/`); this pass's own fetch of `opencode.ai/docs/config` additionally reported `.opencode/skills/` and `~/.config/opencode/skills/` as discovery locations — the two passes disagree on the exact directory name (`.agents/skills` vs `.opencode/skills`) and this was not reconciled `[unverified — likely both work as aliases, not confirmed]`.

---

## 5. Claude Code (reference)

Source: local docs clone `.cache/cc-research-clones/ccdocs/hooks.md`. Included for comparison only, per the task's request.

### Config locations
`~/.claude/settings.json` (user, all projects), `.claude/settings.json` (project, committable), `.claude/settings.local.json` (project, gitignored) — precedence local > project > user (`ccdocs/hooks.md:255-257,722-724`).

### Events relevant to this contract
`PreToolUse` (before a tool call, can block), `PermissionRequest`, `PermissionDenied`, `PostToolUse` (after success), `PostToolUseFailure` (after failure — a **dedicated event**, unlike Codex/Gemini/OpenCode), `UserPromptSubmit`, `Stop`/`StopFailure`, `PreCompact`, `SessionStart` (`ccdocs/hooks.md:22-63`). File read/edit have no dedicated event name either — they're `PreToolUse`/`PostToolUse` matched on `tool_name` (e.g. `Edit|Write`, per the matcher example at `ccdocs/hooks.md:309`).

### Exact stdin example (`ccdocs/hooks.md:776-798`, a `PreToolUse` Bash call)
```json
{
  "session_id": "abc123",
  "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
  "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
  "cwd": "/home/user/my-project",
  "scratchpad_dir": "/tmp/claude-1000/-home-user-my-project/abc123/scratchpad",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "npm test", "description": "Run test suite", "timeout": 120000, "run_in_background": false },
  "tool_use_id": "toolu_01ABC123..."
}
```
Command string at `tool_input.command`; file path for `Edit`/`Write` inside `tool_input` (key name not re-confirmed in this excerpt). `session_id` and `cwd` are top-level on every event.

### Output contract
- **Deny**: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}` (`ccdocs/hooks.md:108-111,217-220`).
- **Exit code 2**: blocking, independent of JSON — "even a JSON `permissionDecision` of `"allow"` can't override it" (`ccdocs/hooks.md:826`). Exit 2 behavior is per-event: `PreToolUse` blocks the tool call; `UserPromptSubmit` blocks and **erases** the prompt; `PreCompact` blocks compaction; `PostToolUse`/`PostToolUseFailure` don't block (the tool already ran/failed) but surface stderr to Claude (`ccdocs/hooks.md:876-905`). Exit code 1 (or any non-2) without valid JSON is treated as a non-blocking warning, not a policy enforcement — "If your hook is meant to enforce a policy, use `exit 2`" (`ccdocs/hooks.md:864`).
- **Add context**: `hookSpecificOutput.additionalContext`, wrapped in a system reminder and inserted at the point the hook fired (`ccdocs/hooks.md:1004-1012`).
- **Stop-equivalent**: `{"decision": "block", "reason": "..."}` on `Stop`/`SubagentStop`, which also accept `hookSpecificOutput.additionalContext` for non-error feedback that lets the conversation continue rather than treating it as an error (`ccdocs/hooks.md:1047,1075-1079`).
- **Timeouts**: default 600s for `command`/`http`/`mcp_tool` hooks, 30s for `prompt`, 60s for `agent`; lowered to 30s on `UserPromptSubmit`/`PreModelSwitch`/`PostModelSwitch`, 10s on `MessageDisplay`; `SessionEnd` hooks share a 1.5s budget (`ccdocs/hooks.md:430`).

### Rules and skills
`CLAUDE.md` (can also read `AGENTS.md` if `pluginConfigs["agents-md@builtin"].options.instructionFiles` is set, per the matrix doc's correction, `ccdocs/claude-directory.md:1437,1453`). Skills: `SKILL.md` under `.claude/skills/` (project) and `~/.claude/skills/` (user) (`ccdocs/hooks.md:1211-1217`).

---

## Cross-host summary table

| Trigger | Codex | Gemini CLI | Cursor | OpenCode | Claude Code |
|---|---|---|---|---|---|
| Before tool/shell | `PreToolUse` (tool_name=`Bash`) | `BeforeTool` (tool_name=`run_shell_command`) | `beforeShellExecution` / `preToolUse` | `tool.execute.before` (input.tool=`bash`) | `PreToolUse` (tool_name=`Bash`) |
| After tool success | `PostToolUse` | `AfterTool` | `postToolUse` | `tool.execute.after` | `PostToolUse` |
| After tool failure | same event as success `[unverified separate signal]` | same event as success `[unverified separate signal]` | **`postToolUseFailure`** (dedicated) | `[unverified — no typed discriminant found]` | **`PostToolUseFailure`** (dedicated) |
| User prompt submitted | `UserPromptSubmit` | `BeforeAgent` | `beforeSubmitPrompt` | **none found** | `UserPromptSubmit` |
| Agent about to stop | `Stop` (blockable) | `AfterAgent` (`continue:false`/deny+retry) | `stop` | **none found** | `Stop` (blockable) |
| Before compaction | `PreCompact` (blockable) | `PreCompress` (advisory only) | `preCompact` | `experimental.session.compacting` | `PreCompact` (blockable, exit 2) |
| Session start | `SessionStart` | `SessionStart` | `sessionStart` | **none found** | `SessionStart` |
| File read | no dedicated event `[unverified]` | `BeforeTool`/`AfterTool` (tool_name=`read_file`) | `beforeReadFile` | `tool.execute.before` (input.tool=`read` `[unverified]`) | `PreToolUse`/`PostToolUse` (tool_name matcher) |
| File edit | `PreToolUse`/`PostToolUse` (tool_name=`apply_patch`) | `BeforeTool`/`AfterTool` (tool_name=`write_file`/`replace`) | `afterFileEdit` (no before-edit event found) | `tool.execute.before` (input.tool=`edit`/`write` `[unverified]`) | `PreToolUse`/`PostToolUse` (tool_name matcher `Edit|Write`) |
| Block/deny mechanism | JSON `permissionDecision:"deny"` + non-empty reason | exit 2 **or** JSON `decision:"deny"`+reason | JSON `permission:"deny"` or exit 2 | thrown `Error` in the hook function | exit 2 (always blocks) or JSON `permissionDecision:"deny"` |
| Three-way ask/allow/deny at pre-tool layer | **no** (`ask` explicitly rejected) | yes, via separate policy engine, not hooks | **yes** (`permission:"ask"`) | yes, via `permission.ask` hook and the `permission` config key | yes (`permissionDecision:"ask"`) |

## Biggest gap for a single-engine harness

OpenCode has no plugin-level session-start, user-prompt-gate, or stop-gate hook at all (verified against the shipped `Hooks` interface, not just docs). A harness that ships one policy source assuming those three triggers exist everywhere must degrade OpenCode's coverage for those three to the `AGENTS.md` instructions layer and the `permission` config key, not a hook — and say so explicitly rather than silently no-op.
