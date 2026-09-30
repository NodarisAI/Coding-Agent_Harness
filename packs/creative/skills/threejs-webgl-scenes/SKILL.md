---
name: threejs-webgl-scenes
description: >
  Build 3D and WebGL experiences for the web with Three.js and React Three Fiber
  — shader planes, GLTF model scenes, particle fields, scroll-driven cameras,
  postprocessing, and the performance budgets that keep them shippable. Use this
  skill whenever the user wants 3D on a website, mentions Three.js, R3F, WebGL,
  GLSL, shaders, Spline, Blender-to-web, GLTF/GLB, point clouds, particles, or a
  "3D hero"; whenever they want an interactive product viewer, a rotating object,
  a distortion or displacement effect, or a scene the camera moves through on
  scroll; and whenever a page needs depth that CSS transforms cannot produce.
  Also use it when a WebGL scene is slow, blank, black, blurry, drains battery,
  or fails on mobile.
---

# Three.js & WebGL Scenes

Most "3D websites" do not need a 3D scene. A shader on a full-screen plane, or
flat layers in CSS perspective, delivers the same impression at a fraction of the
cost and complexity. Reach for a real scene when the user needs to *inspect*
something — a product, a space, an object with volume. Reach for a shader plane
when they want atmosphere.

Start by asking which it is. Building a GLTF pipeline for something that wanted a
gradient shader is the most common way this work goes wrong.

## Escalation ladder

| Need | Use | Cost |
|---|---|---|
| Depth, atmosphere, distortion | Full-screen shader plane | ~1 draw call |
| Layered depth, parallax | CSS 3D transforms — no WebGL at all | Compositor only |
| One object the user rotates | Three.js + GLTF + OrbitControls | Moderate |
| A scene the camera travels through | R3F + `@react-three/drei` + scroll | High |
| Designer-authored scene | Spline embed, or export GLTF | Varies |

`web-motion-primitives/assets/webgl-hero-plane.html` is a complete, tested
shader-plane hero. If that is what the user needs, start from it rather than
from scratch.

## Vanilla Three.js: the parts that matter

```js
import * as THREE from 'three';

const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));   // ← the important line
renderer.setSize(w, h, false);
```

**Clamp the pixel ratio.** This is the highest-leverage line in any WebGL page.
An uncapped 3× display renders nine times the pixels of 1× for a difference
nobody perceives through a blur or a bloom. On a shader-heavy scene, clamping to
2 is often the entire difference between a hot laptop and a cool one; clamping to
1.5 is defensible for anything soft-focused.

**Antialiasing is expensive and frequently unnecessary.** Turn it off for shader
planes and blurred scenes. Turn it on for hard-edged geometry where jaggies are
visible.

**A full-screen shader needs no camera maths.** Use an `OrthographicCamera`, a
`PlaneGeometry(2,2)`, and write `gl_Position = vec4(position, 1.0)` in the vertex
shader — the plane already spans clip space.

**Paint a frame before the loop starts.** A page that loads in a background tab
has `document.hidden === true`; if the render loop early-returns on that, the
canvas never gets a first frame and ships as a black rectangle. Gate the *loop*
on visibility, but always draw once on asset load, on resize, and on becoming
visible. This bug is invisible in development and obvious to a user who opened
your link in a new tab.

**Always ship a fallback.** WebGL is blocked, unavailable or disabled more often
than assumed. Put a static image behind the canvas and remove the canvas rather
than leaving an empty one:

```js
if (matchMedia('(prefers-reduced-motion: reduce)').matches || !window.WebGLRenderingContext) {
  canvas.remove();   // the poster underneath becomes the hero
}
```

## Texture handling

```js
new THREE.TextureLoader().load(url, (tex) => {
  tex.colorSpace = THREE.SRGBColorSpace;   // omit this and everything looks washed out
  material.uniforms.uTex.value = tex;
  draw();                                   // paint immediately, don't wait for the loop
});
```

`background-size: cover` has no shader equivalent, so compute it yourself or the
texture stretches at every aspect ratio but the image's own:

```glsl
vec2 uv = (vUv - 0.5) * uCover + 0.5;
```

```js
const screen = w / h, image = tex.image.width / tex.image.height;
uCover.set(screen > image ? 1 : image / screen,
           screen > image ? screen / image : 1);
```

## React Three Fiber

```bash
npm i three @react-three/fiber @react-three/drei
```

```jsx
<Canvas dpr={[1, 2]} gl={{ antialias: false }} camera={{ position: [0, 0, 3], fov: 45 }}>
  <Suspense fallback={null}>
    <Scene />
  </Suspense>
</Canvas>
```

`dpr={[1, 2]}` is the R3F form of the pixel-ratio clamp.

Two rules that prevent most R3F performance problems:

**Never `setState` in `useFrame`.** It re-renders the React tree 60 times a
second. Mutate refs directly — `meshRef.current.rotation.y += delta` — because
the render loop is outside React's model by design.

**Create nothing inside `useFrame`.** A `new THREE.Vector3()` per frame is 3,600
allocations a second feeding the garbage collector, which shows up as periodic
stutter. Hoist them.

Useful `drei` helpers: `useGLTF` (loading with suspense), `Environment` (IBL
lighting in one line), `ScrollControls` + `useScroll` (scroll-driven cameras),
`Float`, `MeshTransmissionMaterial` (glass), `Preload all`.

## Models

Compress or don't ship. A raw GLTF export from Blender is routinely 40 MB.

```bash
npx gltf-transform optimize in.glb out.glb --texture-compress webp
npx gltfjsx model.glb --transform     # also emits a typed R3F component
```

Draco for geometry, KTX2/Basis for textures, WebP as the simpler fallback.
Budget: **under 5 MB** for a hero model, under 1 MB for a decorative one. Ask
for the polygon count and texture resolution before accepting an asset — a 2M
triangle model of something displayed at 400px is a conversation to have with
whoever exported it, not a problem to solve in code.

## Performance budget

- Draw calls under ~100 — merge geometry or use `InstancedMesh` for repeats
- One shadow-casting light at most; bake the rest
- Postprocessing is full-screen passes; each one costs a full render of the frame
- Dispose on unmount: geometries, materials, textures, render targets. R3F handles
  its own tree, but anything you created imperatively is yours to clean up
- Stop rendering when off-screen — an IntersectionObserver around the canvas

Measure rather than guess: `renderer.info.render` gives you calls and triangles,
and Chrome DevTools' performance panel will show whether you are GPU- or
CPU-bound. "It feels slow" is not a diagnosis.

## Mobile

Assume half the GPU and a third of the memory. The realistic options are a
reduced scene (fewer particles, no postprocessing, lower DPR) or the static
fallback. Deciding *which* is a product question — ask, rather than shipping a
scene that overheats phones.

`powerPreference: 'high-performance'` on the renderer helps on multi-GPU laptops;
it does nothing on mobile.

## Debugging

| Symptom | Cause |
|---|---|
| Black canvas | Texture not loaded yet, no first draw, or nothing in the frustum |
| Washed-out colours | Missing `tex.colorSpace = SRGBColorSpace` |
| Texture stretched | No cover-fit calculation |
| Fans spin up | Unclamped `devicePixelRatio` |
| Stutter every few seconds | Allocations inside the frame loop |
| Fine on desktop, dies on mobile | No reduced path; check draw calls and texture memory |
| Blank after a route change | Renderer not disposed, or canvas unmounted mid-frame |

## Working with the user

3D is judged visually and nowhere else. Serve it, screenshot it, and iterate one
variable at a time. Ask for a reference — "like this Awwwards site" is far more
actionable than "make it 3D", and it settles the scope question (atmosphere vs.
inspectable object) in one exchange.

Related: `gsap-scroll-motion` for driving a camera from scroll,
`web-motion-primitives` for the tested shader-plane hero, and
`motion-asset-studio` for generating the textures a scene displays.
