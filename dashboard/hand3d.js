// SIGNOVA 3D hand (three.js, vendored). Right hand, palm toward the viewer.
//
// Joint mapping matches the firmware / docs: every joint is normalised 0..1.
//   finger curl c  -> phalanx angles c * [85°, 100°, 70°]
//   thumb          -> thumb_rot swings the thumb from "out to the side" to "across the palm",
//                     thumb bend curls its three segments by c * [20°, 55°, 60°]
//   wrist          -> forearm rotation, (w - 0.5) * 140°
//   spread_index_middle / wrist_flex are drawn when the hand config has them.
// Motion uses the same minimum-jerk curve and per-joint speed limit as firmware/src/motion.cpp.

import * as THREE from "./vendor/three.module.min.js";

const DEG = Math.PI / 180;
export const FINGER_CURL = [85, 100, 70];
export const THUMB_CURL = [20, 55, 60];

export function minJerk(u) {
  u = Math.min(1, Math.max(0, u));
  return u * u * u * (10 - 15 * u + 6 * u * u);
}

const FINGERS = [
  { key: "index", x: 1.45, y: 4.0, lens: [1.55, 1.0, 0.8], r: 0.38 },
  { key: "middle", x: 0.5, y: 4.2, lens: [1.75, 1.1, 0.85], r: 0.39 },
  { key: "ring", x: -0.5, y: 4.05, lens: [1.6, 1.0, 0.8], r: 0.37 },
  { key: "pinky", x: -1.45, y: 3.7, lens: [1.2, 0.8, 0.7], r: 0.33 },
];
const THUMB = { base: [1.75, 0.95, 0.25], lens: [1.35, 1.05, 0.85], r: 0.42 };
const THUMB_OPEN = new THREE.Vector3(0.78, 0.62, 0).normalize();
const THUMB_ACROSS = new THREE.Vector3(-0.62, 0.5, 0.6).normalize();
const THUMB_BEND = new THREE.Vector3(-0.75, -0.25, 0.6);

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export class Hand3D {
  /**
   * @param {HTMLElement} container
   * @param {{joints: string[], rest: Record<string, number>, fullRangeMs?: number, onUpdate?: Function}} opts
   */
  constructor(container, opts) {
    this.container = container;
    this.joints = opts.joints;
    this.rest = { ...opts.rest };
    this.fullRangeMs = opts.fullRangeMs ?? 250;
    this.onUpdate = opts.onUpdate || null;
    this.pose = { ...this.rest };
    this.moving = new Set();
    this.anim = null;
    this.dirty = true;
    this.reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.ok = this._init();
  }

  _init() {
    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    } catch (e) {
      const div = document.createElement("div");
      div.className = "fallback";
      div.textContent = "3D view unavailable (WebGL is disabled). The joint bars still show every pose.";
      this.container.append(div);
      return false;
    }
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.container.append(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(34, 1, 0.1, 200);
    this.target = new THREE.Vector3(0, 2.9, 0);
    this.home = { yaw: 24 * DEG, pitch: 12 * DEG, r: 23 }; // slight 3/4 view so finger curls read clearly
    this.orbit = { ...this.home };

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x556070, 1.5));
    const key = new THREE.DirectionalLight(0xffffff, 2.0);
    key.position.set(5, 9, 12);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xffffff, 0.7);
    rim.position.set(-8, 4, -6);
    this.scene.add(rim);

    this.baseMat = new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.08 });
    this.jointMat = new THREE.MeshStandardMaterial({ roughness: 0.4, metalness: 0.2 });
    this.liveColor = new THREE.Color();
    this._applyTheme();

    this.root = new THREE.Group(); // wrist rotation (pronation / supination)
    this.scene.add(this.root);
    const forearm = new THREE.Mesh(new THREE.CylinderGeometry(1.3, 1.5, 3.8, 32), this.baseMat);
    forearm.position.y = -1.75;
    this.root.add(forearm);
    this.handGroup = new THREE.Group(); // wrist flex
    this.root.add(this.handGroup);

    const palm = new THREE.Mesh(new THREE.BoxGeometry(4.1, 4.25, 1.05), this.baseMat);
    palm.position.set(0, 2.05, 0);
    this.handGroup.add(palm);
    const thenar = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 16), this.baseMat);
    thenar.scale.set(0.95, 1.2, 0.42);
    thenar.position.set(1.05, 1.15, 0.3);
    this.handGroup.add(thenar);

    this.fingerParts = {};
    for (const f of FINGERS) {
      const base = new THREE.Group();
      base.position.set(f.x, f.y, 0);
      this.handGroup.add(base);
      this.fingerParts[f.key] = { base, segs: this._chain(base, f.lens, f.r, f.key) };
    }
    this.thumbBase = new THREE.Group();
    this.thumbBase.position.set(...THUMB.base);
    this.handGroup.add(this.thumbBase);
    this.fingerParts.thumb = { base: this.thumbBase, segs: this._chain(this.thumbBase, THUMB.lens, THUMB.r, "thumb") };

    this._resize();
    this.ro = new ResizeObserver(() => this._resize());
    this.ro.observe(this.container);
    this._bindOrbit();
    new MutationObserver(() => { this._applyTheme(); this.dirty = true; })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { this._applyTheme(); this.dirty = true; });

    this._applyPose();
    const loop = (now) => {
      this._tick(now);
      if (this.dirty) {
        this.renderer.render(this.scene, this.camera);
        this.dirty = false;
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    return true;
  }

  _chain(parent, lens, radius, key) {
    const segs = [];
    let p = parent;
    lens.forEach((len, i) => {
      const jointGroup = new THREE.Group();
      if (i > 0) jointGroup.position.y = lens[i - 1];
      p.add(jointGroup);
      const mat = this.baseMat.clone();
      mat.userData.key = key;
      const r = radius * (1 - i * 0.07);
      const mesh = new THREE.Mesh(new THREE.CapsuleGeometry(r, Math.max(0.05, len - r * 0.6), 6, 20), mat);
      mesh.position.y = len / 2;
      jointGroup.add(mesh);
      if (i > 0) {
        const knuckle = new THREE.Mesh(new THREE.SphereGeometry(r * 0.55, 12, 8), this.jointMat);
        knuckle.position.z = r * 0.75;
        jointGroup.add(knuckle);
      }
      segs.push({ group: jointGroup, mat });
      p = jointGroup;
    });
    return segs;
  }

  _applyTheme() {
    if (!this.baseMat) return;
    this.baseMat.color.set(cssVar("--hand", "#DCE2E9"));
    this.jointMat.color.set(cssVar("--joint", "#8A97A5"));
    this.liveColor.set(cssVar("--live", "#D9771A"));
    for (const part of Object.values(this.fingerParts || {})) {
      for (const s of part.segs) s.mat.color.copy(this.baseMat.color);
    }
  }

  _bindOrbit() {
    const el = this.renderer.domElement;
    let drag = null;
    el.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, ...this.orbit }; el.setPointerCapture(e.pointerId); });
    el.addEventListener("pointermove", (e) => {
      if (!drag) return;
      this.orbit.yaw = Math.max(-100 * DEG, Math.min(100 * DEG, drag.yaw - (e.clientX - drag.x) * 0.01));
      this.orbit.pitch = Math.max(-35 * DEG, Math.min(60 * DEG, drag.pitch + (e.clientY - drag.y) * 0.01));
      this._placeCamera();
    });
    const end = () => { drag = null; };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("dblclick", () => { this.orbit = { ...this.home }; this._placeCamera(); });
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.orbit.r = Math.max(13, Math.min(36, this.orbit.r + Math.sign(e.deltaY) * 1.2));
      this._placeCamera();
    }, { passive: false });
  }

  _placeCamera() {
    const { yaw, pitch, r } = this.orbit;
    this.camera.position.set(
      this.target.x + r * Math.sin(yaw) * Math.cos(pitch),
      this.target.y + r * Math.sin(pitch),
      this.target.z + r * Math.cos(yaw) * Math.cos(pitch),
    );
    this.camera.lookAt(this.target);
    this.dirty = true;
  }

  _resize() {
    const w = this.container.clientWidth || 300;
    const h = this.container.clientHeight || 300;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this._placeCamera();
  }

  _val(name) {
    const v = this.pose[name];
    return typeof v === "number" ? v : (this.rest[name] ?? 0);
  }

  _applyPose() {
    if (!this.ok) return;
    for (const f of FINGERS) {
      const c = this._val(f.key);
      this.fingerParts[f.key].segs.forEach((s, i) => { s.group.rotation.x = c * FINGER_CURL[i] * DEG; });
    }
    const spread = this.joints.includes("spread_index_middle") ? this._val("spread_index_middle") : 0;
    this.fingerParts.index.base.rotation.z = -spread * 12 * DEG;
    this.fingerParts.middle.base.rotation.z = spread * 10 * DEG;

    // Thumb: orientation from thumb_rot, then curl from thumb bend.
    const t = this._val("thumb_rot");
    const d = THUMB_OPEN.clone().lerp(THUMB_ACROSS, t).normalize();
    const f = THUMB_BEND.clone().addScaledVector(d, -THUMB_BEND.dot(d)).normalize();
    const x = new THREE.Vector3().crossVectors(d, f).normalize();
    this.thumbBase.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, d, f));
    const tb = this._val("thumb");
    this.fingerParts.thumb.segs.forEach((s, i) => { s.group.rotation.x = tb * THUMB_CURL[i] * DEG; });

    this.root.rotation.y = (this._val("wrist") - 0.5) * 140 * DEG;
    this.handGroup.rotation.x = this.joints.includes("wrist_flex") ? (this._val("wrist_flex") - 0.5) * 80 * DEG : 0;

    for (const [key, part] of Object.entries(this.fingerParts)) {
      const hot = this.moving.has(key) || (key === "thumb" && this.moving.has("thumb_rot"));
      for (const s of part.segs) s.mat.emissive.copy(hot ? this.liveColor : new THREE.Color(0x000000)).multiplyScalar(hot ? 0.45 : 0);
    }
    this.dirty = true;
  }

  _tick(now) {
    const a = this.anim;
    if (!a) return;
    let allDone = true;
    for (const j of Object.keys(a.to)) {
      const dur = a.dur[j];
      const u = dur <= 0 ? 1 : (now - a.t0) / dur;
      if (u < 1) allDone = false;
      this.pose[j] = a.from[j] + (a.to[j] - a.from[j]) * minJerk(u);
    }
    if (allDone) {
      this.moving.clear();
      this.anim = null;
      a.resolve(true);
    }
    this._applyPose();
    if (this.onUpdate) this.onUpdate(this.pose, this.moving);
  }

  /** Animate to a pose over `ms`, like the firmware (min-jerk, per-joint speed limit). */
  animateTo(target, ms) {
    if (this.anim) { this.anim.resolve(false); this.anim = null; }
    const to = {};
    const from = {};
    const dur = {};
    this.moving.clear();
    for (const j of this.joints) {
      if (typeof target[j] !== "number") continue;
      from[j] = this._val(j);
      to[j] = Math.min(1, Math.max(0, target[j]));
      dur[j] = this.reduceMotion ? 0 : Math.max(ms, Math.abs(to[j] - from[j]) * this.fullRangeMs);
      if (Math.abs(to[j] - from[j]) > 0.02) this.moving.add(j);
    }
    return new Promise((resolve) => {
      this.anim = { from, to, dur, t0: performance.now(), resolve };
      if (!this.ok) { Object.assign(this.pose, to); this.anim = null; this.moving.clear(); resolve(true); if (this.onUpdate) this.onUpdate(this.pose, this.moving); }
    });
  }

  setPose(pose) {
    if (this.anim) { this.anim.resolve(false); this.anim = null; }
    this.moving.clear();
    for (const j of this.joints) if (typeof pose[j] === "number") this.pose[j] = Math.min(1, Math.max(0, pose[j]));
    this._applyPose();
    if (this.onUpdate) this.onUpdate(this.pose, this.moving);
  }

  getPose() {
    return { ...this.pose };
  }
}
