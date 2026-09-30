# Third-party notices

The harness is Nodaris AI's own work (see LICENSE). Parts of it are adapted from, or were informed by, the open-source
projects below. Code or text copied from them keeps its original licence, which governs that part; everything else
was rebuilt in our own words and structure.

## Included with their licences

| Component | Where | Source | Licence |
|---|---|---|---|
| pstack skills | `packs/core/vendor/pstack/` | [michael-denyer/pstack-claude](https://github.com/michael-denyer/pstack-claude) at `c02fd49` | MIT; the licence and the upstream notices are in that folder |
| Test-driven development, grill-me and triage skills | `packs/core/skills/pocock-tdd/`, `pocock-grill-me/`, `pocock-triage/` | [mattpocock/skills](https://github.com/mattpocock/skills) | MIT, reproduced below |

## Rebuilt from ideas (no code copied)

| What we built | Idea from | Licence of the source |
|---|---|---|
| `design` and `investigate` skills, `ship-review` | pstack (`architect`, `arena`, `how`, `why`, review and ship skills) | MIT |
| Design review with an adversarial reviewer and debate | [chaseai-yt/claudex-loop](https://github.com/chaseai-yt/claudex-loop), [NulightJens/rocket-fuel-skill](https://github.com/NulightJens/rocket-fuel-skill) | Not declared to GitHub; MIT |
| Minimal-code ladder in the rules | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | MIT |
| Spec gate before autonomous work | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | MIT |
| `plain-copy` skill and copy lint | [nateherkai/human-speak](https://github.com/nateherkai/human-speak) | MIT |
| Design lint | [pbakaus/impeccable](https://github.com/pbakaus/impeccable) | Apache-2.0 |
| `nodaris-harness tips` | [nateherkai/token-dashboard](https://github.com/nateherkai/token-dashboard) | MIT |
| `product-film` skill | [latent-spaces/brag](https://github.com/latent-spaces/brag) | MIT |
| Motion principles | [lottiefiles/motion-design-skill](https://github.com/lottiefiles/motion-design-skill) | MIT |
| Palette colour-difference check | [zanwei/design-dna](https://github.com/zanwei/design-dna) | MIT |
| One-sentence motion intent | [AThevon/genjutsu](https://github.com/AThevon/genjutsu) | Not declared to GitHub |
| Terminal splash screens | an "animation-skill" listing on the MCP market (rich and pyfiglet) | Not stated |
| Reversible delete | a hooks gist by NulightJens | Not stated |

## Tools the harness can install or call, never bundled

code-review-graph and graphify (installed with uv only after the person agrees), Hyperframes (Apache-2.0) and
Remotion (its own licence; larger companies need a paid company licence) are named by the creative and graph skills
with pinned versions. They are not part of this repository.

## MIT licence text for mattpocock/skills

```
MIT License

Copyright (c) 2026 Matt Pocock

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
