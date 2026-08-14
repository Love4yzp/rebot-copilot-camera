import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import URDFLoader from "urdf-loader";
import type { URDFRobot } from "urdf-loader";

const URDF_PACKAGE_ROOT = "/assets/urdf/00-arm-rs_asm-v3";
const URDF_URL = `${URDF_PACKAGE_ROOT}/urdf/00-arm-rs_asm-v3.urdf`;

const GHOST_OPACITY = 0.35;
const GHOST_TARGET_OPACITY = 0.85;
//: Display-side follow time constant, seconds. The live arm streams at 20 Hz;
//: setting joint values directly makes the monitor step between samples. A
//: one-pole follow at 60 Hz glides the drawn arm between samples (and tweens
//: preview/pose switches) without touching the control loop's timing.
const SMOOTH_TAU = 0.06;

export interface GhostPose {
  id: string;
  name: string;
  joints: Record<string, number>;
}

interface GhostEntry {
  pose: GhostPose;
  robot: URDFRobot;
  end: THREE.Object3D | null;
  label: HTMLDivElement;
}

interface Props {
  positions: Record<string, number>;
  preview?: Record<string, number> | null;
  ghosts?: GhostPose[];
  targetPoseId?: string | null;
  targetAmber?: boolean;
  onGhostClick?: (pose: GhostPose) => void;
}

export function ArmView3D({ positions, preview, ghosts, targetPoseId, targetAmber, onGhostClick }: Props) {
  const mount = useRef<HTMLDivElement>(null);
  const robot = useRef<URDFRobot | null>(null);
  const [status, setStatus] = useState("加载模型…");

  const sceneRef = useRef<THREE.Scene | null>(null);
  const ghostMapRef = useRef<Map<string, GhostEntry>>(new Map());
  const ctlRef = useRef<{
    preset: (name: "reset" | "operator") => void;
    frameGhost: (entry: GhostEntry) => void;
  } | null>(null);
  const [follow, setFollow] = useState(false);
  const followRef = useRef(follow);
  followRef.current = follow;
  const [showGhosts, setShowGhosts] = useState(true);
  const onClickRef = useRef(onGhostClick);
  onClickRef.current = onGhostClick;
  /** The pose the drawn arm should glide toward — preview wins over live. */
  const targetJointsRef = useRef<Record<string, number> | null>(null);
  targetJointsRef.current = preview ?? positions;
  const prevTargetRef = useRef<string | null>(null);
  /** Loads are never cancelled by effect re-runs (20 Hz broadcasts rebuild the
   * deps array); only unmount cancels. */
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  const activeIdsRef = useRef<Set<string>>(new Set());
  const inflightRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const host = mount.current;
    if (!host) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0d12);
    sceneRef.current = scene;
    ghostMapRef.current.clear();

    const camera = new THREE.PerspectiveCamera(45, 4 / 3, 0.05, 40);
    camera.position.set(0.85, 0.7, 0.85);
    camera.lookAt(0, 0.2, 0);

    scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x14181f, 2.0));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1.2, 2, 1);
    scene.add(key);

    const grid = new THREE.GridHelper(2, 20, 0x2a3341, 0x1a2029);
    scene.add(grid);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch (error) {
      setStatus(`3D 无法初始化 — ${error instanceof Error ? error.message : String(error)}`);
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    host.appendChild(renderer.domElement);

    let theta = Math.PI / 4;
    let phi = Math.PI / 3.4;
    let radius = 1.35;
    const lookTarget = new THREE.Vector3(0, 0.22, 0);
    let frameEntry: GhostEntry | null = null;

    const place = () => {
      camera.position.set(
        radius * Math.sin(phi) * Math.cos(theta),
        radius * Math.cos(phi),
        radius * Math.sin(phi) * Math.sin(theta),
      );
      camera.lookAt(lookTarget);
    };
    place();
    const presets = {
      reset: () => {
        theta = Math.PI / 4; phi = Math.PI / 3.4; radius = 1.35;
        lookTarget.set(0, 0.22, 0); frameEntry = null; place();
      },
      operator: () => {
        theta = 0.25; phi = 1.25; radius = 1.8;
        lookTarget.set(0, 0.18, 0); frameEntry = null; place();
      },
    };
    ctlRef.current = {
      preset: (name) => presets[name](),
      frameGhost: (entry) => { frameEntry = entry; },
    };

    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let moved = 0;
    const onDown = (e: PointerEvent) => {
      dragging = true;
      moved = 0;
      lastX = e.clientX;
      lastY = e.clientY;
    };
    const onMove = (e: PointerEvent) => {
      if (!dragging) return;
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      moved += Math.abs(dx) + Math.abs(dy);
      theta -= dx * 0.008;
      phi = Math.min(Math.PI / 2, Math.max(0.15, phi - dy * 0.008));
      lastX = e.clientX;
      lastY = e.clientY;
      frameEntry = null;
      place();
    };
    const onUp = () => {
      if (dragging && moved < 6) tapAt(lastX, lastY);
      dragging = false;
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      radius = Math.min(3.5, Math.max(0.5, radius + e.deltaY * 0.0016));
      frameEntry = null;
      place();
    };

    const raycaster = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const tapAt = (clientX: number, clientY: number) => {
      const rect = renderer.domElement.getBoundingClientRect();
      ndc.set(
        ((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1,
      );
      raycaster.setFromCamera(ndc, camera);
      const hits = raycaster.intersectObjects(scene.children, true);
      for (const hit of hits) {
        const entry = hit.object.userData.ghostEntry as GhostEntry | undefined;
        if (!entry) continue;
        frameEntry = entry;
        highlightGhost(entry.pose.id, true);
        window.setTimeout(() => highlightGhost(entry.pose.id, false), 1500);
        onClickRef.current?.(entry.pose);
        break;
      }
    };

    const loader = new URDFLoader();
    loader.workingPath = `${URDF_PACKAGE_ROOT}/`;
    loader.packages = URDF_PACKAGE_ROOT;

    let meshTimer = 0;
    loader.load(
      URDF_URL,
      (loaded) => {
        loaded.rotation.x = -Math.PI / 2;
        loaded.traverse((child) => {
          const mesh = child as THREE.Mesh;
          if (mesh.isMesh) {
            mesh.material = new THREE.MeshStandardMaterial({
              color: 0x9aa7b8, metalness: 0.25, roughness: 0.55,
            });
          }
        });
        scene.add(loaded);
        robot.current = loaded;
        setStatus("");

        meshTimer = window.setTimeout(() => {
          let meshes = 0;
          loaded.traverse((child) => {
            if ((child as THREE.Mesh).isMesh) meshes += 1;
          });
          if (meshes === 0) setStatus("模型网格缺失 — 检查 submodule 是否 init 过");
        }, 3000);
      },
      undefined,
      () => setStatus("模型加载失败 — 检查 submodule 是否 init 过"),
    );

    const highlightGhost = (id: string, on: boolean) => {
      const entry = ghostMapRef.current.get(id);
      if (!entry) return;
      entry.robot.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (mesh.isMesh) {
          (mesh.material as THREE.MeshStandardMaterial).opacity =
            on ? GHOST_TARGET_OPACITY : GHOST_OPACITY;
        }
      });
      entry.label.classList.toggle("amber", on);
    };
    ghostHooksRef.current = { highlightGhost, scene };

    renderer.domElement.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    renderer.domElement.addEventListener("wheel", onWheel, { passive: false });

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = host;
      if (w === 0 || h === 0) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    const v = new THREE.Vector3();
    const targetPos = new THREE.Vector3();
    const projectLabels = () => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      for (const entry of ghostMapRef.current.values()) {
        if (!entry.end) { entry.label.style.display = "none"; continue; }
        entry.end.getWorldPosition(v);
        v.project(camera);
        if (v.z > 1) { entry.label.style.display = "none"; continue; }
        entry.label.style.display = "block";
        entry.label.style.left = `${((v.x + 1) / 2) * w}px`;
        entry.label.style.top = `${((-v.y + 1) / 2) * h}px`;
      }
    };

    let frame = 0;
    let displayJoints: Record<string, number | undefined> | null = null;
    let lastNow = performance.now();
    const tick = () => {
      frame = requestAnimationFrame(tick);
      const now = performance.now();
      const dt = Math.min((now - lastNow) / 1000, 0.1);
      lastNow = now;

      const loaded = robot.current;
      const target = targetJointsRef.current;
      if (loaded && target) {
        if (!displayJoints) displayJoints = { ...target };
        const alpha = dt > 0 ? 1 - Math.exp(-dt / SMOOTH_TAU) : 1;
        for (const [name, value] of Object.entries(target)) {
          if (!loaded.joints[name]) continue;
          const cur = displayJoints[name] ?? value;
          const next = cur + (value - cur) * alpha;
          displayJoints[name] = next;
          loaded.setJointValue(name, next);
        }
      }

      if (frameEntry && frameEntry.end) {
        frameEntry.end.getWorldPosition(targetPos);
        const offset = new THREE.Vector3(0.75, 0.85, 0.75).multiplyScalar(radius);
        camera.position.lerp(targetPos.clone().add(offset), 0.06);
        lookTarget.lerp(targetPos, 0.06);
        camera.lookAt(lookTarget);
      } else {
        place();
      }
      projectLabels();
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(meshTimer);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("wheel", onWheel);
      for (const entry of ghostMapRef.current.values()) {
        scene.remove(entry.robot);
        entry.label.remove();
        disposeRobot(entry.robot);
      }
      ghostMapRef.current.clear();
      ctlRef.current = null;
      ghostHooksRef.current = null;
      renderer.dispose();
      host.removeChild(renderer.domElement);
    };
  }, []);

  const ghostHooksRef = useRef<{
    highlightGhost: (id: string, on: boolean) => void;
    scene: THREE.Scene;
  } | null>(null);

  useEffect(() => {
    const list = showGhosts ? ghosts ?? [] : [];
    activeIdsRef.current = new Set(list.map((g) => g.id));

    // Drop ghosts whose pose no longer exists.
    for (const [id, entry] of ghostMapRef.current) {
      if (!activeIdsRef.current.has(id)) {
        ghostMapRef.current.delete(id);
        entry.label.remove();
        entry.robot.removeFromParent();
        disposeRobot(entry.robot);
      }
    }
    // Re-apply joints (a re-taught pose moves its ghost in place).
    for (const pose of list) {
      const entry = ghostMapRef.current.get(pose.id);
      if (entry) applyJoints(entry.robot, pose.joints);
    }
    // Load the new ones; the inflight set guards against double-loading while
    // a broadcast re-renders this effect's deps array.
    for (const pose of list) {
      if (ghostMapRef.current.has(pose.id) || inflightRef.current.has(pose.id)) continue;
      inflightRef.current.add(pose.id);
      void loadGhostRobot(pose)
        .then((robot) => {
          inflightRef.current.delete(pose.id);
          if (!mountedRef.current || !activeIdsRef.current.has(pose.id)) {
            disposeRobot(robot);
            return;
          }
          robot.traverse((child) => {
            const mesh = child as THREE.Mesh;
            if (mesh.isMesh) {
              mesh.material = new THREE.MeshStandardMaterial({
                color: 0x9aa7b8, metalness: 0.25, roughness: 0.55,
                transparent: true, opacity: GHOST_OPACITY, depthWrite: false,
              });
              mesh.userData.ghostEntry = null;
            }
          });
          const entry: GhostEntry = {
            pose,
            robot,
            end: robot.getObjectByName("link6") ?? null,
            label: makeGhostLabel(pose.name),
          };
          robot.traverse((child) => {
            const mesh = child as THREE.Mesh;
            if (mesh.isMesh) mesh.userData.ghostEntry = entry;
          });
          ghostMapRef.current.set(pose.id, entry);
          mount.current?.appendChild(entry.label);
          sceneRef.current?.add(robot);
        })
        .catch((error) => {
          inflightRef.current.delete(pose.id);
          console.warn("ghost pose load failed", pose.id, error);
          setStatus((prev) => (prev ? prev : "部分位姿模型加载失败 — 检查 submodule 是否 init 过"));
        });
    }
  }, [ghosts, showGhosts]);

  useEffect(() => {
    const hooks = ghostHooksRef.current;
    if (!hooks) return;
    const prev = prevTargetRef.current;
    if (prev && prev !== targetPoseId) hooks.highlightGhost(prev, false);
    prevTargetRef.current = targetPoseId ?? null;
    if (!targetPoseId) return;
    const entry = ghostMapRef.current.get(targetPoseId);
    if (!entry) return;
    hooks.highlightGhost(targetPoseId, true);
    entry.label.classList.toggle("amber", !!targetAmber);
    if (followRef.current) ctlRef.current?.frameGhost(entry);
  }, [targetPoseId, targetAmber]);

  return (
    <div className="viewer" ref={mount}>
      <div className="viewer__ctl">
        <button type="button" onClick={() => ctlRef.current?.preset("reset")}>复位视角</button>
        <button type="button" onClick={() => ctlRef.current?.preset("operator")}>操作者视角</button>
        <button
          type="button"
          className={follow ? "on" : undefined}
          onClick={() => setFollow((v) => !v)}
        >
          {follow ? "跟随目标：开" : "跟随目标：关"}
        </button>
        <button type="button" onClick={() => setShowGhosts((v) => !v)}>
          {showGhosts ? "幽灵位姿：开" : "幽灵位姿：关"}
        </button>
      </div>
      {status ? <div className="overlay">{status}</div> : null}
    </div>
  );
}

function makeGhostLabel(name: string): HTMLDivElement {
  const label = document.createElement("div");
  label.className = "viewer__glabel";
  label.textContent = name;
  return label;
}

function applyJoints(robot: URDFRobot, joints: Record<string, number>) {
  for (const [name, value] of Object.entries(joints)) {
    if (robot.joints[name]) robot.setJointValue(name, value);
  }
}

function disposeRobot(robot: URDFRobot) {
  robot.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry.dispose();
    const mat = mesh.material as THREE.Material | THREE.Material[];
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
    else mat.dispose();
  });
}

function loadGhostRobot(pose: GhostPose): Promise<URDFRobot> {
  return new Promise((resolve, reject) => {
    const loader = new URDFLoader();
    loader.workingPath = `${URDF_PACKAGE_ROOT}/`;
    loader.packages = URDF_PACKAGE_ROOT;
    loader.load(URDF_URL, (robot) => {
      robot.rotation.x = -Math.PI / 2;
      applyJoints(robot, pose.joints);
      waitMeshes(robot).then(() => resolve(robot));
    }, undefined, reject);
  });
}

function waitMeshes(root: THREE.Object3D): Promise<number> {
  return new Promise((resolve) => {
    let tries = 0;
    const timer = window.setInterval(() => {
      let n = 0;
      root.traverse((c) => { if ((c as THREE.Mesh).isMesh) n += 1; });
      if (n > 0 || ++tries > 150) {
        window.clearInterval(timer);
        resolve(n);
      }
    }, 200);
  });
}
