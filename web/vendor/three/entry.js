// Subset of three.js r187 used by web/front3d.js. Rebuild with:
//   npx esbuild entry.js --bundle --format=esm --minify --legal-comments=inline --outfile=three-lite.js
export {
  WebGLRenderer, Scene, PerspectiveCamera, InstancedMesh, SphereGeometry,
  MeshLambertMaterial, MeshBasicMaterial, Color, Object3D, AmbientLight,
  DirectionalLight, BoxGeometry, EdgesGeometry, LineSegments, LineBasicMaterial,
  BufferGeometry, Float32BufferAttribute, Sprite, SpriteMaterial, CanvasTexture,
  Raycaster, Vector2, Vector3, Mesh, SRGBColorSpace, REVISION,
} from 'three';
export { OrbitControls } from 'three/addons/controls/OrbitControls.js';
