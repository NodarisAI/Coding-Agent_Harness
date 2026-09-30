# Motion principles for interface work

Adapted from the LottieFiles motion design skill (lottiefiles/motion-design-skill, MIT) and the classic animation
principles, rewritten for product interfaces. Use it when choosing timing and easing, and when a motion "feels off"
but nobody can say why.

## Timing
| Interaction | Duration |
|---|---|
| Hover, press, focus ring, toggle, checkbox | 120-200 ms |
| Tooltip, dropdown, small card or toast entering | 180-250 ms |
| Modal, drawer, panel, larger card | 240-320 ms |
| Page or route transition, hero reveal | 350-500 ms, rarely |
| Exits | about two thirds of the matching entry |

Distance changes duration: a 16px move and a 600px move should not share a timing. Larger travel gets more time, up
to the limits above. Repeated interactions (typing, list navigation) get the shortest times or none.

## Easing
- **Ease-out** for entrances and responses to input: `cubic-bezier(.22, 1, .36, 1)` or `cubic-bezier(.16, 1, .3, 1)`.
- **Ease-in** for exits only: `cubic-bezier(.4, 0, 1, 1)`.
- **Ease-in-out** for an object moving from one resting place to another on screen.
- **Linear** only for continuous loops such as a spinner or a progress stripe.
- **Springs** (a physics library or `linear()` with sampled points) for drag and gesture follow-through.

## The principles, mapped to UI
| Principle | In an interface |
|---|---|
| Timing | Durations above; motion never blocks the next action. |
| Easing (slow in, slow out) | One shared curve per product, set as a token. |
| Staging | One focal motion per view; stagger groups by 30-60 ms. |
| Anticipation | A 2-4% scale-down on press before the response. |
| Follow-through and overlapping action | A small overshoot that settles; child elements arrive 30-60 ms after their container. |
| Arcs | Large moves travel on a slight curve rather than a straight diagonal. |
| Squash and stretch | Almost never in product UI; reserve for playful brands. |
| Secondary action | A shadow deepening as a card lifts; an icon rotating as a menu opens. |
| Exaggeration | Only in onboarding or marketing moments, never in daily workflows. |
| Solid drawing and appeal | Motion keeps layout stable: no text reflow, no layout shift during animation. |

## Tokens
```css
:root {
  --dur-quick: 150ms;
  --dur-base: 240ms;
  --dur-slow: 400ms;
  --ease-out: cubic-bezier(.22, 1, .36, 1);
  --ease-in: cubic-bezier(.4, 0, 1, 1);
  --ease-in-out: cubic-bezier(.65, 0, .35, 1);
  --stagger: 45ms;
}
@media (prefers-reduced-motion: reduce) {
  :root { --dur-quick: 0ms; --dur-base: 0ms; --dur-slow: 0ms; --stagger: 0ms; }
}
```

Animate `transform` and `opacity` only; they run on the compositor and do not cause layout. Reduced motion replaces
movement with an instant change or a short fade; it is a designed state, not a missing one.
