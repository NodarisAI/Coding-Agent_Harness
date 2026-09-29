# Terminal motion libraries (versions checked 2026-09-29)

Pin the exact version in the lock file. Never run `npx`, `uvx` or `pipx run` without a version, and install nothing
new without asking. Versions below are the latest published releases on the date above, read from PyPI, npm and the
GitHub releases API; check for a newer release before adding one to a project.

## Python
| Library | Pin | Use it for | Notes |
|---|---|---|---|
| rich | `rich==15.0.0` | Styled text, `Progress`, `Status` spinners, tables, panels | `Console()` already honours `NO_COLOR`, `TERM=dumb` and non-TTY; progress bars are transient with `transient=True`. |
| textual | `textual==8.2.8` | Full-screen terminal applications with layout and animation | Only for an interactive TUI, never for a one-shot command. |
| pyfiglet | `pyfiglet==1.0.4` | Large ASCII banners from figlet fonts | Measure the rendered width against the terminal before printing; fall back to plain text. |
| standard library | none | Escapes, `termios`/`tty` for key reads, `select`, `signal`, `shutil.get_terminal_size` | Enough for a banner and a spinner; see `examples/splash.py`. |

## Node
| Library | Pin | Use it for | Notes |
|---|---|---|---|
| ink | `ink@7.1.1` | React components rendered to the terminal | For interactive TUIs. |
| chalk | `chalk@6.0.1` | Colour and styles | Detects colour support, including `NO_COLOR` and `FORCE_COLOR`. ESM only. |
| gradient-string | `gradient-string@3.0.0` | Gradient text for banners | Check `chalk.level` or the TTY yourself before calling it. |
| ora | `ora@9.4.1` | Spinners | Disables itself when not a TTY or in CI; end with `succeed()` or `fail()` so a result line remains. |
| cli-spinners | `cli-spinners@3.4.0` | Spinner frame sets only | When you draw the spinner yourself. |

## Go
| Library | Pin | Use it for | Notes |
|---|---|---|---|
| Lip Gloss | `charm.land/lipgloss/v2 v2.0.6` | Styles, borders, layout, adaptive colours | v2 module path is `charm.land/...`, not `github.com/...`. |
| Bubble Tea | `charm.land/bubbletea/v2 v2.0.10` | Interactive TUIs (Elm architecture) | Restores the terminal on exit; still handle your own cleanup. |
| Bubbles, Harmonica | latest tagged release | Spinner, progress and viewport components; spring-based animation | From the same Charm organisation. |
| Gum | `gum v2.0.2` | Styled prompts, spinners and banners in shell scripts | `brew install gum`; useful when the tool is a shell script. |

## Choosing
- A one-shot command that prints a banner and a spinner: the standard library, or rich if it is already a
  dependency.
- A Node CLI: chalk plus ora; add gradient-string only for a first-run banner.
- An interactive full-screen tool: textual (Python), ink (Node) or Bubble Tea with Lip Gloss (Go).
