import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import URDFLoader from "urdf-loader";
import type { URDFRobot } from "urdf-loader";

const URDF_URL = "/assets/urdf/00-arm-rs_asm-v3/urdf/00-arm-rs_asm-v3.urdf";

interface Props {
  /** Live joint angles from the control loop. */
  positions: Record<string, number>;
  /** A stored waypoint being previewed, drawn instead of the live pose. */
  preview?: Record<string, number> | null;
}

/**
 * URDF viewer, following the asset layout `rebot_arm_webui` uses.
 *
 * The meshes are 63 MB, so they are served from the vendored submodule rather
 * than bundled, and the loader is given the package root so its relative mesh
 * references resolve.
 *
 * When a waypoint is selected the viewer shows that pose instead of the live
 * one. Comparing "where it is" against "where it would go" is the question an
 * operator actually has before pressing play.
 */
export function ArmView3D({ positions, preview }: Props) {
  const mount = useRef<HTMLDivElement>(null);
  const robot = useRef<URDFRobot | null>(null);
  const [status, setStatus] = useState("加载模型…");

  useEffect(() => {
    const host = mount.current;
    if (!host) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0d12);

    const camera = new THREE.PerspectiveCamera(45, 4 / 3, 0.05, 40);
    camera.position.set(0.85, 0.7, 0.85);
    camera.lookAt(0, 0.2, 0);

    scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x14181f, 2.0));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1.2, 2, 1);
    scene.add(key);

    const grid = new THREE.GridHelper(2, 20, 0x2a3341, 0x1a2029);
    scene.add(grid);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    host.appendChild(renderer.domElement);

    const loader = new URDFLoader();
    // URDF mesh paths are relative to the package root, not to the .urdf file.
    loader.packages = "/assets/urdf/00-arm-rs_asm-v3";

    loader.load(
      URDF_URL,
      (loaded) => {
        // URDF is Z-up; three.js is Y-up.
        loaded.rotation.x = -Math.PI / 2;
        loaded.traverse((child) => {
          const mesh = child as THREE.Mesh;
          if (mesh.isMesh) {
            mesh.material = new THREE.MeshStandardMaterial({
              color: 0x9aa7b8,
              metalness: 0.25,
              roughness: 0.55,
            });
          }
        });
        scene.add(loaded);
        robot.current = loaded;
        setStatus("");
      },
      undefined,
      () => setStatus("模型加载失败 — 检查 submodule 是否 init 过"),
    );

    // Drag to orbit. Deliberately hand-rolled rather than pulling in
    // OrbitControls: this view only ever needs to be spun and zoomed.
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let theta = Math.PI / 4;
    let phi = Math.PI / 3.4;
    let radius = 1.35;

    const place = () => {
      camera.position.set(
        radius * Math.sin(phi) * Math.cos(theta),
        radius * Math.cos(phi),
        radius * Math.sin(phi) * Math.sin(theta),
      );
      camera.lookAt(0, 0.22, 0);
    };
    place();

    const onDown = (e: PointerEvent) => {
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
    };
    const onMove = (e: PointerEvent) => {
      if (!dragging) return;
      theta -= (e.clientX - lastX) * 0.008;
      phi = Math.min(Math.PI - 0.15, Math.max(0.15, phi - (e.clientY - lastY) * 0.008));
      lastX = e.clientX;
      lastY = e.clientY;
      place();
    };
    const onUp = () => {
      dragging = false;
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      radius = Math.min(3.5, Math.max(0.5, radius + e.deltaY * 0.0016));
      place();
    };

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

    let frame = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("wheel", onWheel);
      renderer.dispose();
      host.removeChild(renderer.domElement);
    };
  }, []);

  // Drive the model. Runs on every state broadcast, so it must stay cheap.
  useEffect(() => {
    const loaded = robot.current;
    if (!loaded) return;

    const pose = preview ?? positions;
    for (const [name, value] of Object.entries(pose)) {
      // `gripper` is one motor driving two prismatic finger joints, with no
      // calibrated angle-to-travel mapping, so it is not driven here.
      if (loaded.joints[name]) loaded.setJointValue(name, value);
    }
  }, [positions, preview]);

  return (
    <div className="viewer" ref={mount}>
      <div className="overlay">{status || (preview ? "预览选中点位" : "实时姿态")}</div>
    </div>
  );
}
