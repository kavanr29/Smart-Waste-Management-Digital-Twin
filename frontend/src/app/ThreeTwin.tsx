'use client';

import { Fragment, memo, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import {
  ContactShadows,
  Html,
  Line,
  OrbitControls,
  PerspectiveCamera,
} from '@react-three/drei';
import * as THREE from 'three';
import { api } from '../lib/api';

export type TwinBin = {
  id: number;
  code: string;
  name: string;
  zone: string;
  target_stream?: string;
  fill_pct: number;
  fill_kg: number;
  capacity_kg: number;
  status: string;
  contamination: number;
  accumulation_rate: number;
  overflow_hours: number | null;
  overflow_risk: number | null;
  priority_score: number;
  priority_label: string;
  priority_reasons: string[];
  last_collection?: string;
};

type Vec3 = [number, number, number];

type BuildingSpec = {
  name: string;
  position: Vec3;
  width: number;
  depth: number;
  height: number;
  color: string;
  floors?: number;
  glass?: boolean;
  balconies?: boolean;
};

type RoutePoint = Vec3;

const BUILDINGS: BuildingSpec[] = [
  {
    name: 'Medical Block · CDSIMER',
    position: [-22, 0, -60],
    width: 36,
    depth: 34,
    height: 20,
    color: '#e7dfcf',
    floors: 4,
    glass: true,
  },
  {
    name: 'School of Engineering',
    position: [48, 0, -66],
    width: 46,
    depth: 22,
    height: 20,
    color: '#d6c8ad',
    floors: 4,
  },
  {
    name: 'AIML Block',
    position: [50, 0, -16],
    width: 28,
    depth: 20,
    height: 20,
    color: '#e6dcc9',
    floors: 4,
    glass: true,
  },
  {
    name: 'Girls Hostel',
    position: [-133, 0, -11],
    width: 25,
    depth: 28,
    height: 25,
    color: '#e0d2b5',
    floors: 5,
    balconies: true,
  },
  {
    name: 'Boys Hostel',
    position: [-92, 0, -11],
    width: 25,
    depth: 28,
    height: 25,
    color: '#d7c6a8',
    floors: 5,
    balconies: true,
  },
  {
    name: 'Staff Residence',
    position: [-40, 0, -3],
    width: 22,
    depth: 18,
    height: 15,
    color: '#e2d6c0',
    floors: 3,
    balconies: true,
  },
  {
    name: 'Food Court & Cafeteria',
    position: [141, 0, -8],
    width: 20,
    depth: 50,
    height: 10,
    color: '#f0d9a8',
    floors: 2,
    glass: true,
  },
  {
    name: 'Admissions',
    position: [-26, 0, 46],
    width: 24,
    depth: 18,
    height: 15,
    color: '#e4d8c3',
    floors: 3,
  },
  {
    name: 'Pharmacy Block',
    position: [41, 0, 46],
    width: 24,
    depth: 18,
    height: 15,
    color: '#e7eeea',
    floors: 3,
    glass: true,
  },
  {
    name: 'Campus Bank & ATM',
    position: [10, 0, 98],
    width: 14,
    depth: 12,
    height: 10,
    color: '#e8dcc4',
    floors: 2,
  },
  {
    name: 'Waste Yard',
    position: [-135, 0, 92],
    width: 24,
    depth: 20,
    height: 5,
    color: '#b8beb9',
    floors: 1,
  },
  {
    name: 'Sports & Auditorium',
    position: [103, 0, 88],
    width: 45,
    depth: 30,
    height: 15,
    color: '#d9e4dc',
    floors: 3,
    glass: true,
  },
];

const ZONE_POS: Record<string, Vec3> = {
  'academic zone': [48, 0, -66],
  'hostel zone': [-112, 0, -11],
  'cafeteria zone': [141, 0, -8],
  'sports zone': [103, 0, 88],
  'parking zone': [-90, 0, 94],
  'administration zone': [-26, 0, 46],
  'library zone': [15, 0, 8],
  'garden / outdoor zone': [0, 0, 58],
  'central plaza': [15, 0, 8],
  'block a': [-22, 0, -60],
  'block b': [41, 0, 46],
  'block c': [50, 0, -16],
  unassigned: [0, 0, 0],
};

const FLOW_POS: Record<string, Vec3> = {
  collection: [-135, 0.5, 92],
  sorting: [-106, 0.5, 82],
  recycling: [-78, 0.5, 82],
  recovery: [-78, 0.5, 100],
  disposal: [-112, 0.5, 108],
};

const STATUS_COLOR: Record<string, string> = {
  EMPTY: '#6f9b81',
  LOW: '#2e9e57',
  MEDIUM: '#e0a30b',
  HIGH: '#f29b50',
  'VERY HIGH': '#e65d43',
  CRITICAL: '#d93f3a',
};

function zoneKey(zone: string) {
  const key = (zone || 'Unassigned').trim().toLowerCase();

  if (key === 'block a') return 'academic zone';
  if (key === 'block b') return 'library zone';
  if (key === 'block c') return 'cafeteria zone';

  return key;
}

function binPosition(bin: TwinBin, bins: TwinBin[]): Vec3 {
  const key = zoneKey(bin.zone);
  const sameZone = bins.filter((item) => zoneKey(item.zone) === key);
  const index = Math.max(
    0,
    sameZone.findIndex((item) => item.id === bin.id),
  );

  const center = ZONE_POS[key] || ZONE_POS.unassigned;
  const columns = 4;
  const column = index % columns;
  const row = Math.floor(index / columns);

  return [
    center[0] + (column - 1.5) * 3.4,
    0.08,
    center[2] + 13 + row * 3.1,
  ];
}

const ROAD_X = [-158, -65, 75, 160];
const ROAD_Z = [-138, -99, -36, 30, 108];
const DEPOT: RoutePoint = [-65, 0.45, -36];

function nearestRoadPoint(position: Vec3): RoutePoint {
  const roadX = ROAD_X.reduce((closest, value) =>
    Math.abs(value - position[0]) < Math.abs(closest - position[0])
      ? value
      : closest,
  );
  const roadZ = ROAD_Z.reduce((closest, value) =>
    Math.abs(value - position[2]) < Math.abs(closest - position[2])
      ? value
      : closest,
  );

  return [roadX, 0.45, roadZ];
}

function collectionRoadRoute(bins: TwinBin[], allBins: TwinBin[]): RoutePoint[] {
  const points: RoutePoint[] = [DEPOT];
  let current = DEPOT;

  for (const bin of bins) {
    const binLocation = binPosition(bin, allBins);
    const roadStop = nearestRoadPoint(binLocation);
    const approach: RoutePoint = [
      roadStop[0] + (binLocation[0] - roadStop[0]) * 0.72,
      0.45,
      roadStop[2] + (binLocation[2] - roadStop[2]) * 0.72,
    ];
    const alongX: RoutePoint = [roadStop[0], 0.45, current[2]];
    const alongZ: RoutePoint = [roadStop[0], 0.45, roadStop[2]];

    if (alongX[0] !== current[0] || alongX[2] !== current[2]) {
      points.push(alongX);
    }
    if (alongZ[0] !== alongX[0] || alongZ[2] !== alongX[2]) {
      points.push(alongZ);
    }
    points.push(approach, approach);
    current = roadStop;
  }

  if (current[0] !== DEPOT[0] || current[2] !== DEPOT[2]) {
    points.push([DEPOT[0], 0.45, current[2]], DEPOT);
  }

  return points;
}

function Label({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`twin-label ${className}`}>{children}</div>;
}

function RendererLifecycle() {
  const { gl, invalidate } = useThree();

  useEffect(() => {
    gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));

    const canvas = gl.domElement;
    const preventLoss = (event: Event) => event.preventDefault();
    const restoreContext = () => invalidate();

    canvas.addEventListener('webglcontextlost', preventLoss);
    canvas.addEventListener('webglcontextrestored', restoreContext);

    return () => {
      canvas.removeEventListener('webglcontextlost', preventLoss);
      canvas.removeEventListener('webglcontextrestored', restoreContext);
    };
  }, [gl, invalidate]);

  return null;
}

const Building = memo(function Building({
  spec,
}: {
  spec: BuildingSpec;
}) {
  const {
    name,
    position,
    width: w,
    depth: d,
    height: h,
    color,
    glass,
    balconies,
  } = spec;

  const floors = spec.floors ?? Math.max(1, Math.round(h / 5));
  const windowsPerFloor = Math.max(3, Math.floor(w / 4.2));

  return (
    <group position={position}>
      {/* Building body */}
      <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[w, h, d]} />
        <meshStandardMaterial color={color} roughness={0.82} />
      </mesh>

      {/* Raised foundation */}
      <mesh position={[0, 0.28, 0]} castShadow receiveShadow>
        <boxGeometry args={[w + 1.2, 0.56, d + 1.2]} />
        <meshStandardMaterial color="#a7a69c" roughness={0.92} />
      </mesh>

      {/* Roof slab */}
      <mesh position={[0, h + 0.25, 0]} castShadow receiveShadow>
        <boxGeometry args={[w + 1, 0.5, d + 1]} />
        <meshStandardMaterial color="#b9b8ae" roughness={0.8} />
      </mesh>

      {/* Roof parapets */}
      {[-1, 1].map((side) => (
        <mesh
          key={`parapet-${side}`}
          position={[0, h + 0.55, side * (d / 2)]}
        >
          <boxGeometry args={[w + 0.7, 0.2, 0.2]} />
          <meshStandardMaterial color="#d5d0c4" />
        </mesh>
      ))}

      {/* Front facade windows */}
      {Array.from({ length: floors }).map((_, floor) =>
        Array.from({ length: windowsPerFloor }).map((__, index) => {
          const wx =
            -w / 2 + ((index + 0.5) * w) / windowsPerFloor;
          const wy = 1.5 + floor * ((h - 2.6) / floors);
          const ww = Math.min(2.25, w / windowsPerFloor - 0.5);
          const wh = Math.min(2.25, ((h - 2.8) / floors) * 0.68);

          return (
            <group key={`window-${floor}-${index}`}>
              <mesh position={[wx, wy, d / 2 + 0.04]}>
                <boxGeometry args={[ww + 0.18, wh + 0.18, 0.1]} />
                <meshStandardMaterial color="#f0ebdf" />
              </mesh>

              <mesh position={[wx, wy, d / 2 + 0.1]}>
                <boxGeometry args={[ww, wh, 0.045]} />
                <meshStandardMaterial
                  color={glass ? '#638b98' : '#526b77'}
                  metalness={0.3}
                  roughness={0.24}
                  transparent
                  opacity={0.88}
                />
              </mesh>

              <mesh position={[wx, wy, d / 2 + 0.13]}>
                <boxGeometry args={[0.055, wh, 0.025]} />
                <meshStandardMaterial color="#e6e2d7" />
              </mesh>

              {balconies && (
                <mesh
                  position={[wx, wy - wh / 2 - 0.14, d / 2 + 0.4]}
                >
                  <boxGeometry args={[ww + 0.3, 0.12, 0.7]} />
                  <meshStandardMaterial color="#c5b9a2" />
                </mesh>
              )}
            </group>
          );
        }),
      )}

      {/* Structural facade bays */}
      {Array.from({ length: Math.max(2, Math.floor(w / 9)) }).map((_, index, array) => {
        const x = -w / 2 + (index + 0.5) * (w / array.length);
        return (
          <mesh
            key={`facade-column-${index}`}
            position={[x, h / 2, d / 2 + 0.16]}
            castShadow
          >
            <boxGeometry args={[0.22, h - 0.8, 0.18]} />
            <meshStandardMaterial color="#d2cbbb" roughness={0.68} />
          </mesh>
        );
      })}

      {/* Side windows */}
      {Array.from({
        length: Math.max(2, Math.floor(d / 4.5)),
      }).map((_, index, array) => {
        const wz =
          -d / 2 + ((index + 0.5) * d) / array.length;

        return (
          <group key={`side-window-${index}`}>
            <mesh position={[w / 2 + 0.04, h * 0.56, wz]}>
              <boxGeometry args={[0.08, Math.min(2.3, h * 0.2), 1.7]} />
              <meshStandardMaterial color="#6d8f9a" roughness={0.3} />
            </mesh>

            <mesh position={[-w / 2 - 0.04, h * 0.56, wz]}>
              <boxGeometry args={[0.08, Math.min(2.3, h * 0.2), 1.7]} />
              <meshStandardMaterial color="#6d8f9a" roughness={0.3} />
            </mesh>
          </group>
        );
      })}

      {/* Main entrance */}
      <mesh position={[0, 1.05, d / 2 + 0.2]} castShadow>
        <boxGeometry args={[Math.min(3.2, w * 0.22), 2.1, 0.22]} />
        <meshStandardMaterial color="#4f5b57" />
      </mesh>

      <mesh position={[0, 0.22, d / 2 + 0.55]} receiveShadow>
        <boxGeometry args={[Math.min(4.6, w * 0.3), 0.42, 1.05]} />
        <meshStandardMaterial color="#9b9b91" roughness={0.9} />
      </mesh>

      {/* Entrance canopy */}
      <mesh position={[0, 2.25, d / 2 + 0.72]} castShadow>
        <boxGeometry args={[Math.min(5.2, w * 0.42), 0.22, 1.4]} />
        <meshStandardMaterial color="#d7d1c2" roughness={0.5} />
      </mesh>

      {/* Rooftop HVAC units */}
      {[0, 1].map((index) => (
        <group
          key={`hvac-${index}`}
          position={[(index - 0.5) * Math.min(w * 0.35, 8), h + 0.65, 0]}
        >
          <mesh castShadow>
            <boxGeometry args={[2.2, 0.7, 1.8]} />
            <meshStandardMaterial color="#8c9693" roughness={0.75} />
          </mesh>
          <mesh position={[0, 0.38, 0]}>
            <boxGeometry args={[1.8, 0.08, 1.45]} />
            <meshStandardMaterial color="#58645f" />
          </mesh>
        </group>
      ))}

      <Html
        position={[0, h + 2, 0]}
        center
        distanceFactor={58}
        style={{ pointerEvents: 'none' }}
      >
        <Label className="campus-building-label">{name}</Label>
      </Html>
    </group>
  );
});

function Tree({
  position,
  scale = 1,
  flowering = false,
}: {
  position: Vec3;
  scale?: number;
  flowering?: boolean;
}) {
  const foliage = flowering
    ? ['#b76d8e', '#d58eaa', '#e0a6bb']
    : ['#245b35', '#347442', '#47864e'];

  return (
    <group position={position} scale={scale}>
      <mesh position={[0, 2.1, 0]} castShadow>
        <cylinderGeometry args={[0.35, 0.55, 4.2, 8]} />
        <meshStandardMaterial color="#6d4c32" roughness={0.95} />
      </mesh>

      {[
        [0, 4.2, 0, 2.1],
        [-1.15, 3.85, 0.1, 1.35],
        [1.1, 3.8, -0.1, 1.45],
        [0.15, 5.1, 0.35, 1.35],
      ].map((item, index) => (
        <mesh
          key={index}
          position={[item[0], item[1], item[2]]}
          castShadow
        >
          <icosahedronGeometry args={[item[3], 1]} />
          <meshStandardMaterial
            color={foliage[index % foliage.length]}
            roughness={1}
          />
        </mesh>
      ))}
    </group>
  );
}

function Road({
  position,
  size,
}: {
  position: Vec3;
  size: [number, number];
}) {
  return (
    <group position={position}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={size} />
        <meshStandardMaterial color="#454b48" roughness={0.98} />
      </mesh>
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, 0.025, 0]}
      >
        <planeGeometry args={[size[0] * 0.018, size[1]]} />
        <meshBasicMaterial color="#c9c6a8" />
      </mesh>
    </group>
  );
}

function CampusDetails() {
  const trees: Vec3[] = [
    [-160, 0.05, -75],
    [-150, 0.05, -45],
    [-148, 0.05, 15],
    [-152, 0.05, 68],
    [-72, 0.05, -83],
    [-48, 0.05, -91],
    [8, 0.05, -92],
    [80, 0.05, -95],
    [126, 0.05, -78],
    [163, 0.05, -48],
    [164, 0.05, 22],
    [151, 0.05, 57],
    [-62, 0.05, 24],
    [-52, 0.05, 64],
    [69, 0.05, 18],
    [78, 0.05, 58],
    [20, 0.05, 121],
    [55, 0.05, 120],
    [145, 0.05, 111],
    [-20, 0.05, 119],
    [-166, 0.05, 105],
    [-62, 0.05, 112],
    [2, 0.05, 67],
    [123, 0.05, 120],
  ];

  return (
    <>
      {/* Campus grass */}
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, -0.16, 0]}
        receiveShadow
      >
        <planeGeometry args={[390, 300]} />
        <meshStandardMaterial color="#8ea780" roughness={1} />
      </mesh>

      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, -0.1, 0]}
        receiveShadow
      >
        <planeGeometry args={[360, 260]} />
        <meshStandardMaterial color="#a7b895" roughness={1} />
      </mesh>

      {/* Internal road grid */}
      {[-99, -36, 30, 108].map((z) => (
        <Road key={`road-h-${z}`} position={[0, -0.02, z]} size={[320, 7]} />
      ))}

      {[-158, -65, 75, 160].map((x) => (
        <Road key={`road-v-${x}`} position={[x, -0.015, 0]} size={[7, 228]} />
      ))}

      <Road position={[0, -0.01, -138]} size={[352, 14]} />

      {/* Campus boundary walls */}
      {[
        { position: [-131, 1.5, -119] as Vec3, size: [90, 3, 1.2] as Vec3 },
        { position: [47, 1.5, -119] as Vec3, size: [88, 3, 1.2] as Vec3 },
        { position: [139, 1.5, -119] as Vec3, size: [74, 3, 1.2] as Vec3 },
        { position: [0, 1.5, 120] as Vec3, size: [352, 3, 1.2] as Vec3 },
        { position: [-176, 1.5, 0] as Vec3, size: [1.2, 3, 240] as Vec3 },
        { position: [176, 1.5, 0] as Vec3, size: [1.2, 3, 240] as Vec3 },
      ].map((wall, index) => (
        <mesh
          key={`wall-${index}`}
          position={wall.position}
          castShadow
          receiveShadow
        >
          <boxGeometry args={wall.size} />
          <meshStandardMaterial color="#cfc5ae" roughness={0.9} />
        </mesh>
      ))}

      {/* Main campus gates */}
      {[-93, 95].map((x, index) => (
        <group key={`gate-${index}`} position={[x, 0, -119]}>
          {[-8, 8].map((dx) => (
            <mesh key={dx} position={[dx, 4.5, 0]} castShadow>
              <boxGeometry args={[2.6, 9, 2.6]} />
              <meshStandardMaterial color="#e9dfc8" />
            </mesh>
          ))}

          <mesh position={[0, 9.2, 0]} castShadow>
            <boxGeometry args={[19, 2.2, 2.2]} />
            <meshStandardMaterial color="#e9dfc8" />
          </mesh>

          <Html
            position={[0, 12, 0]}
            center
            distanceFactor={60}
            style={{ pointerEvents: 'none' }}
          >
            <Label>
              {index === 0 ? 'Main Entrance' : 'Express Entrance'}
            </Label>
          </Html>
        </group>
      ))}

      {/* Parking area */}
      <group position={[-90, 0, 94]}>
        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, 0.05, 0]}
          receiveShadow
        >
          <planeGeometry args={[40, 20]} />
          <meshStandardMaterial color="#59615b" roughness={0.95} />
        </mesh>

        {Array.from({ length: 8 }).map((_, index) => (
          <mesh
            key={index}
            rotation={[-Math.PI / 2, 0, 0]}
            position={[-16 + index * 4.5, 0.08, 0]}
          >
            <planeGeometry args={[0.15, 17]} />
            <meshBasicMaterial color="#e4dfc5" />
          </mesh>
        ))}
      </group>

      {/* Central plaza and fountain */}
      <group position={[15, 0, 8]}>
        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, 0.08, 0]}
          receiveShadow
        >
          <circleGeometry args={[21, 64]} />
          <meshStandardMaterial color="#b9b7a8" roughness={0.9} />
        </mesh>

        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, 0.16, 0]}
        >
          <circleGeometry args={[12, 64]} />
          <meshStandardMaterial
            color="#3e8fb5"
            metalness={0.35}
            roughness={0.16}
          />
        </mesh>

        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, 0.21, 0]}
        >
          <ringGeometry args={[12, 13.4, 64]} />
          <meshStandardMaterial color="#cfc8b8" />
        </mesh>

        <mesh position={[0, 2.5, 0]}>
          <coneGeometry args={[0.9, 5, 12]} />
          <meshStandardMaterial
            color="#cfefff"
            transparent
            opacity={0.65}
            emissive="#8acfe9"
            emissiveIntensity={0.3}
          />
        </mesh>
      </group>

      {/* Lake */}
      <group position={[139, 0, -85]}>
        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, 0.08, 0]}
        >
          <circleGeometry args={[38.5, 64]} />
          <meshStandardMaterial color="#8a7a5a" roughness={0.95} />
        </mesh>

        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, 0.16, 0]}
        >
          <circleGeometry args={[36, 64]} />
          <meshStandardMaterial
            color="#8fb9c9"
            metalness={0.28}
            roughness={0.16}
          />
        </mesh>
      </group>

      {/* Sports court */}
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[143, 0.04, 88]}
        receiveShadow
      >
        <planeGeometry args={[44, 26]} />
        <meshStandardMaterial color="#567b91" roughness={0.85} />
      </mesh>

      {[-1, 0, 1].map((index) => (
        <mesh
          key={index}
          rotation={[-Math.PI / 2, 0, 0]}
          position={[143, 0.07, 88 + index * 7]}
        >
          <planeGeometry args={[40, 0.18]} />
          <meshBasicMaterial color="#f1ead4" />
        </mesh>
      ))}

      {/* Running track */}
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[50, 0.035, 185]}
        scale={[1.4, 0.8, 1]}
      >
        <circleGeometry args={[32, 64]} />
        <meshStandardMaterial color="#b5452c" roughness={0.9} />
      </mesh>

      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[50, 0.055, 185]}
        scale={[1.4, 0.8, 1]}
      >
        <circleGeometry args={[24, 64]} />
        <meshStandardMaterial color="#8fb878" roughness={1} />
      </mesh>

      {/* Amphitheatre */}
      <group position={[100, 0, -53]}>
        {Array.from({ length: 5 }).map((_, index) => (
          <mesh
            key={index}
            position={[0, 0.35 + index * 0.42, 0]}
          >
            <cylinderGeometry
              args={[
                13 - index * 1.55,
                13 - index * 1.55,
                0.55,
                40,
                1,
                false,
                0,
                Math.PI,
              ]}
            />
            <meshStandardMaterial
              color={index % 2 ? '#d2c8b2' : '#b9ad95'}
              roughness={0.88}
              side={THREE.DoubleSide}
            />
          </mesh>
        ))}

        <mesh position={[0, 0.22, 2]}>
          <cylinderGeometry args={[3.5, 3.5, 0.4, 24]} />
          <meshStandardMaterial color="#8e958c" />
        </mesh>
      </group>

      {/* Sorting stations in waste yard */}
      <group position={[-135, 0, 92]}>
        {['#2e9e57', '#2f7fd1', '#1fa5b8', '#8e5bd1'].map(
          (color, index) => (
            <group
              key={color}
              position={[16, 1.4, -7 + index * 4.6]}
            >
              <mesh castShadow>
                <boxGeometry args={[4.5, 2.8, 3.2]} />
                <meshStandardMaterial color={color} roughness={0.62} />
              </mesh>

              <mesh position={[0, 1.48, 0]}>
                <boxGeometry args={[4.7, 0.16, 3.4]} />
                <meshStandardMaterial color="#e9e9df" />
              </mesh>
            </group>
          ),
        )}
      </group>

      {/* Trees and landscaping */}
      {trees.map((position, index) => (
        <Tree
          key={`tree-${index}`}
          position={position}
          scale={index % 4 === 0 ? 0.8 : 1}
          flowering={index % 9 === 0}
        />
      ))}
    </>
  );
}

function RealisticLighting() {
  return (
    <>
      <hemisphereLight args={['#dbe9ff', '#5d6d4d', 0.82]} />
      <ambientLight intensity={0.25} />

      <directionalLight
        position={[-160, 240, 140]}
        intensity={2}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-250}
        shadow-camera-right={250}
        shadow-camera-top={250}
        shadow-camera-bottom={-250}
        shadow-camera-near={10}
        shadow-camera-far={800}
      />
    </>
  );
}

function BinModel({
  bin,
  position,
  selected,
  recommended,
  onSelect,
}: {
  bin: TwinBin;
  position: Vec3;
  selected: boolean;
  recommended: boolean;
  onSelect: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const color = STATUS_COLOR[bin.status] || STATUS_COLOR.LOW;
  const fill = Math.max(0, Math.min((bin.fill_pct || 0) / 100, 1));

  const streamColors: Record<string, string> = {
    wet: '#2e9e57',
    organic: '#2e9e57',
    dry: '#2f7fd1',
    recyclable: '#1fa5b8',
    'e-waste': '#8e5bd1',
  };

  const stream = (bin.target_stream || 'recyclable').toLowerCase();
  const binColor =
    Object.entries(streamColors).find(([key]) => stream.includes(key))?.[1] ||
    '#1fa5b8';

  return (
    <group
      position={position}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      onPointerOver={(event) => {
        event.stopPropagation();
        setHovered(true);
        document.body.style.cursor = 'pointer';
      }}
      onPointerOut={() => {
        setHovered(false);
        document.body.style.cursor = 'auto';
      }}
    >
      {/* Selection and priority rings */}
      {(recommended || selected || bin.priority_label === 'CRITICAL') && (
        <mesh position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <torusGeometry
            args={[
              recommended ? 2.5 : selected ? 2.1 : 1.75,
              recommended ? 0.18 : 0.11,
              8,
              28,
            ]}
          />
          <meshStandardMaterial
            color={
              recommended
                ? '#f2c875'
                : selected
                  ? '#e8f7e8'
                  : '#ff817c'
            }
            emissive={recommended ? '#9c7425' : '#703431'}
            emissiveIntensity={0.55}
          />
        </mesh>
      )}

      {/* Bin body */}
      <group scale={1.55}>
        <mesh position={[0, 1, 0]} castShadow>
          <cylinderGeometry args={[0.78, 0.72, 1.9, 16]} />
          <meshStandardMaterial
            color={binColor}
            metalness={0.18}
            roughness={0.52}
          />
        </mesh>

        {/* Fill indicator */}
        <mesh position={[0, 1, 0.73]}>
          <boxGeometry args={[0.68, Math.max(0.03, fill * 1.55), 0.04]} />
          <meshStandardMaterial
            color={color}
            transparent
            opacity={0.95}
          />
        </mesh>

        {/* Lid */}
        <mesh position={[0, 1.98, 0]} castShadow>
          <cylinderGeometry args={[0.84, 0.84, 0.18, 16]} />
          <meshStandardMaterial
            color="#26332d"
            metalness={0.45}
            roughness={0.34}
          />
        </mesh>

        {/* Sensor */}
        <mesh position={[0, 2.14, 0]}>
          <cylinderGeometry args={[0.08, 0.08, 0.32, 8]} />
          <meshStandardMaterial color="#d6ded6" metalness={0.4} />
        </mesh>

        <mesh position={[0, 2.42, 0]}>
          <coneGeometry args={[0.55, 0.9, 8]} />
          <meshStandardMaterial
            color={color}
            emissive={color}
            emissiveIntensity={0.35}
          />
        </mesh>
      </group>

      {(hovered || selected) && (
        <Html
          position={[0, 5.4, 0]}
          center
          distanceFactor={48}
          style={{ pointerEvents: 'none' }}
        >
          <Label className="bin-label">
            <b>
              {bin.code}
              {recommended ? ' · RECOMMENDED' : ''}
            </b>
            <span>
              {Math.round(bin.fill_pct)}% fill · {bin.status}
            </span>
            <span>{bin.target_stream || 'recyclable'} stream</span>
          </Label>
        </Html>
      )}
    </group>
  );
}

function StreetLamp({ position }: { position: Vec3 }) {
  return (
    <group position={position}>
      <mesh position={[0, 3, 0]} castShadow>
        <cylinderGeometry args={[0.12, 0.16, 6, 8]} />
        <meshStandardMaterial
          color="#263833"
          metalness={0.7}
          roughness={0.3}
        />
      </mesh>

      <mesh position={[0.6, 5.8, 0]} rotation={[0, 0, -0.35]}>
        <cylinderGeometry args={[0.09, 0.09, 1.3, 8]} />
        <meshStandardMaterial color="#263833" metalness={0.7} />
      </mesh>

      <mesh position={[1.05, 5.55, 0]}>
        <sphereGeometry args={[0.24, 12, 8]} />
        <meshStandardMaterial
          color="#fff1c2"
          emissive="#ffd978"
          emissiveIntensity={1.4}
        />
      </mesh>
    </group>
  );
}

function CollectionTruck({
  route,
  progress,
  active,
}: {
  route: RoutePoint[];
  progress: number;
  active: boolean;
}) {
  const truck = useRef<THREE.Group>(null);

  useFrame(() => {
    if (!truck.current || route.length === 0) return;
    if (route.length < 2) {
      truck.current.position.set(...route[0]);
      return;
    }
    const segment = Math.min(route.length - 2, Math.floor(progress * (route.length - 1)));
    const localProgress = progress * (route.length - 1) - segment;
    const start = route[segment];
    const end = route[segment + 1];
    const x = start[0] + (end[0] - start[0]) * localProgress;
    const z = start[2] + (end[2] - start[2]) * localProgress;
    truck.current.position.set(x, route[0][1], z);
    truck.current.rotation.y = Math.atan2(end[0] - start[0], end[2] - start[2]);
  });

  if (!active && progress < 1) return null;

  return (
    <group ref={truck} position={route[0]}>
      <mesh position={[0, 2.1, 0]} castShadow>
        <boxGeometry args={[4.2, 3, 7]} />
        <meshStandardMaterial color="#2f8f4a" roughness={0.62} metalness={0.15} />
      </mesh>
      <mesh position={[0, 2.2, 4]} castShadow>
        <boxGeometry args={[4.1, 2.8, 2.5]} />
        <meshStandardMaterial color="#e7ece8" roughness={0.45} />
      </mesh>
      <mesh position={[0, 2.75, 5.28]}>
        <boxGeometry args={[3.6, 1, 0.08]} />
        <meshStandardMaterial color="#20333a" metalness={0.35} roughness={0.18} />
      </mesh>
      {[-2, 2].map((x) => (
        <mesh key={x} position={[x, 0.6, -1.8]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[0.7, 0.7, 0.45, 16]} />
          <meshStandardMaterial color="#151a18" roughness={0.85} />
        </mesh>
      ))}
      {[-2, 2].map((x) => (
        <mesh key={`front-${x}`} position={[x, 0.6, 3.4]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[0.7, 0.7, 0.45, 16]} />
          <meshStandardMaterial color="#151a18" roughness={0.85} />
        </mesh>
      ))}
      <mesh position={[0, 4, 4.2]}>
        <sphereGeometry args={[0.34, 12, 8]} />
        <meshStandardMaterial color="#ffb000" emissive="#ffb000" emissiveIntensity={1.4} />
      </mesh>
      <Html position={[0, 5, 0]} center distanceFactor={60} style={{ pointerEvents: 'none' }}>
        <Label className="flow-node-label">COLLECTION TRUCK</Label>
      </Html>
    </group>
  );
}

function Scene({
  bins,
  selected,
  onSelect,
  recommendedBinId,
  flowStages,
  flowCategory,
  showBuildings,
  showRoute,
  truckRoute,
  truckProgress,
  truckActive,
}: {
  bins: TwinBin[];
  selected: TwinBin | null;
  onSelect: (bin: TwinBin) => void;
  recommendedBinId?: number | null;
  flowStages: string[];
  flowCategory: string;
  showBuildings: boolean;
  showRoute: boolean;
  truckRoute: RoutePoint[];
  truckProgress: number;
  truckActive: boolean;
}) {
  const selectedPosition = selected ? binPosition(selected, bins) : null;

  const route = useMemo<Vec3[]>(() => {
    if (flowStages.length === 0) return [];

    const anchor: Vec3 = selectedPosition || [0, 0.5, 0];

    return [
      anchor,
      ...flowStages.map((stage) => FLOW_POS[stage] || FLOW_POS.collection),
    ];
  }, [flowStages.join('|'), selected?.id, bins.length]);

  const flowColor =
    flowCategory === 'Organic'
      ? '#f2c875'
      : flowCategory === 'Other'
        ? '#ff817c'
        : '#7dc6e9';

  const lamps: Vec3[] = [
    [-120, 0, -60],
    [-65, 0, -45],
    [0, 0, -85],
    [65, 0, -40],
    [120, 0, -20],
    [-100, 0, 35],
    [-40, 0, 65],
    [40, 0, 70],
    [115, 0, 70],
    [0, 0, 110],
  ];

  return (
    <>
      <color attach="background" args={['#a9c6d5']} />
      <fog attach="fog" args={['#a9c6d5', 250, 600]} />

      <RealisticLighting />

      <ContactShadows
        position={[0, 0.01, 0]}
        opacity={0.3}
        scale={360}
        blur={2.5}
        far={100}
        resolution={512}
      />

      <CampusDetails />

      {showBuildings &&
        BUILDINGS.map((building) => (
          <Building key={building.name} spec={building} />
        ))}

      {/* Streetlights */}
      {lamps.map((position, index) => (
        <StreetLamp key={index} position={position} />
      ))}

      {/* Waste flow facility pads */}
      {Object.entries(FLOW_POS).map(([stage, position]) => (
        <group key={stage} position={position}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[2.6, 2.6, 0.6, 24]} />
            <meshStandardMaterial color="#7d8980" roughness={0.85} />
          </mesh>

          <mesh position={[0, 0.7, 0]}>
            <cylinderGeometry args={[1.3, 1.3, 0.8, 24]} />
            <meshStandardMaterial
              color={
                stage === 'collection'
                  ? '#5b9b72'
                  : stage === 'sorting'
                    ? '#c7a552'
                    : stage === 'recycling'
                      ? '#5d9fca'
                      : stage === 'recovery'
                        ? '#7eb979'
                        : '#b66b61'
              }
            />
          </mesh>

          <Html
            position={[0, 2, 0]}
            center
            distanceFactor={55}
            style={{ pointerEvents: 'none' }}
          >
            <Label className="flow-node-label">{stage}</Label>
          </Html>
        </group>
      ))}

      {/* Smart bins use backend fill and status values */}
      {bins.map((bin) => (
        <BinModel
          key={bin.id}
          bin={bin}
          position={binPosition(bin, bins)}
          selected={selected?.id === bin.id}
          recommended={recommendedBinId === bin.id}
          onSelect={() => onSelect(bin)}
        />
      ))}

      {/* Collection route */}
      {showRoute && truckRoute.length > 1 && (
        <Line
          points={truckRoute}
          color="#1fb6d9"
          lineWidth={5}
          dashed
          dashSize={4}
          gapSize={2}
        />
      )}

      <CollectionTruck
        route={truckRoute}
        progress={truckProgress}
        active={truckActive}
      />

      {/* Category-specific waste route */}
      {flowStages.length > 0 && route.length > 1 && (
        <>
          <Line
            points={route}
            color={flowColor}
            lineWidth={3}
            dashed
            dashSize={3}
            gapSize={1.5}
          />

          {flowStages.map((stage) => (
            <mesh
              key={`flow-${stage}`}
              position={FLOW_POS[stage] || FLOW_POS.collection}
            >
              <sphereGeometry args={[1.15, 16, 12]} />
              <meshStandardMaterial
                color={flowColor}
                emissive={flowColor}
                emissiveIntensity={0.45}
              />
            </mesh>
          ))}
        </>
      )}
    </>
  );
}

export default function ThreeTwin({
  bins,
  selected,
  onSelect,
  onRefresh,
  recommendedBinId = null,
}: {
  bins: TwinBin[];
  selected: TwinBin | null;
  onSelect: (bin: TwinBin) => void;
  onRefresh?: () => Promise<unknown>;
  recommendedBinId?: number | null;
}) {
  const [showFlow, setShowFlow] = useState(false);
  const [flowCategory, setFlowCategory] = useState('Plastic');
  const [flowStages, setFlowStages] = useState<string[]>([]);
  const [flowNote, setFlowNote] = useState('');
  const [showBuildings, setShowBuildings] = useState(true);
  const [showRoute, setShowRoute] = useState(false);
  const [collectionThreshold, setCollectionThreshold] = useState(70);
  const [truckActive, setTruckActive] = useState(false);
  const [truckProgress, setTruckProgress] = useState(0);
  const [collectionSummary, setCollectionSummary] = useState<{
    count: number;
    kilograms: number;
    destinations: string[];
  } | null>(null);
  const completedDispatch = useRef(false);

  const controls = useRef<any>(null);
  const routeBins = useMemo(
    () =>
      bins
        .filter((bin) => bin.fill_pct >= collectionThreshold)
        .sort((a, b) => b.fill_pct - a.fill_pct),
    [bins, collectionThreshold],
  );
  const routeDisplayBins = useMemo(
    () =>
      routeBins.length > 0
        ? routeBins
        : bins
            .filter((bin) => bin.fill_pct > 0)
            .sort((a, b) => b.fill_pct - a.fill_pct),
    [bins, routeBins],
  );
  const truckRoute = useMemo<RoutePoint[]>(
    () => collectionRoadRoute(routeDisplayBins, bins),
    [bins, routeDisplayBins],
  );
  const routeDistance = Math.round(
    truckRoute.slice(1).reduce((total, point, index) => {
      const previous = truckRoute[index];
      return total + Math.hypot(point[0] - previous[0], point[2] - previous[2]);
    }, 0),
  );

  useEffect(() => {
    if (!truckActive) return;
    const timer = window.setInterval(() => {
      setTruckProgress((value) => {
        if (value >= 1) {
          window.clearInterval(timer);
          setTruckActive(false);
          return 1;
        }
        return Math.min(1, value + 0.008);
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, [truckActive]);

  useEffect(() => {
    if (truckProgress < 1 || completedDispatch.current || routeBins.length === 0) {
      return;
    }

    completedDispatch.current = true;
    const collectBins = async () => {
      try {
        for (const bin of routeBins) {
          await api('/api/v1/collections', {
            method: 'POST',
            body: JSON.stringify({
              bin_id: bin.id,
              collected_by: 'digital-twin-truck',
              notes: 'Collected by the Digital Twin dispatch simulation',
            }),
          });
        }
        const destinations = Array.from(
          new Set(
            routeBins.map((bin) => {
              const stream = (bin.target_stream || '').toLowerCase();
              if (stream.includes('organic') || stream.includes('wet')) {
                return 'recovery / composting';
              }
              if (stream.includes('e-waste')) return 'specialized recovery';
              if (stream.includes('recycl')) return 'recycling';
              return 'sorting and material recovery';
            }),
          ),
        );
        setCollectionSummary({
          count: routeBins.length,
          kilograms: routeBins.reduce((total, bin) => total + bin.fill_kg, 0),
          destinations,
        });
        await onRefresh?.();
      } catch (error) {
        console.error('Truck collection failed:', error);
      }
    };

    void collectBins();
  }, [onRefresh, routeBins, truckProgress]);

  const dispatchTruck = () => {
    if (routeBins.length === 0) return;
    setTruckProgress(0);
    completedDispatch.current = false;
    setTruckActive(true);
    setShowRoute(true);
  };

  // Load category-specific waste flow from the existing API.
  useEffect(() => {
    let active = true;

    if (!showFlow) {
      setFlowStages([]);
      setFlowNote('');
      return () => {
        active = false;
      };
    }

    api<any>(
      `/api/v1/digital-twin/flow?category=${encodeURIComponent(flowCategory)}`,
    )
      .then((response) => {
        if (!active) return;

        setFlowStages(
          (response.pipeline || []).filter(
            (stage: string) => stage in FLOW_POS,
          ),
        );

        setFlowNote('Waste flow loaded from the backend');
      })
      .catch(() => {
        if (active) {
          setFlowStages([]);
          setFlowNote('Could not load waste flow from the backend');
        }
      });

    return () => {
      active = false;
    };
  }, [showFlow, flowCategory]);

  return (
    <div className="three-twin-shell">
      <div className="three-twin-toolbar">
        <div>
          <span className="eyebrow green-text">
            DSU HAROHALLI CAMPUS · ECOTWIN
          </span>
          <small className="twin-count">
            {bins.length} virtual bins · database-backed occupancy
          </small>
          <div className="twin-kpi-row">
            <span><b>{bins.length}</b> bins online</span>
            <span><b>{bins.length ? Math.round(bins.reduce((total, bin) => total + bin.fill_pct, 0) / bins.length) : 0}%</b> avg fill</span>
            <span><b>{routeBins.length}</b> need pickup</span>
            <span><b>{routeDistance} m</b> route</span>
          </div>
        </div>

        <div className="three-twin-controls">
          <button
            className={
              showBuildings
                ? 'secondary twin-toggle active'
                : 'secondary twin-toggle'
            }
            onClick={() => setShowBuildings((value) => !value)}
          >
            {showBuildings ? 'Hide buildings' : 'Show buildings'}
          </button>

          <button
            className={
              showRoute
                ? 'secondary twin-toggle active'
                : 'secondary twin-toggle'
            }
            onClick={() => setShowRoute((value) => !value)}
          >
            {showRoute ? 'Hide collection route' : 'Collection route'}
          </button>
          <label className="secondary twin-toggle collection-threshold">
            Pickup ≥
            <select
              value={collectionThreshold}
              onChange={(event) => setCollectionThreshold(Number(event.target.value))}
            >
              <option value={60}>60%</option>
              <option value={70}>70%</option>
              <option value={80}>80%</option>
            </select>
          </label>
          <button
            className="primary twin-dispatch"
            disabled={routeBins.length === 0 || truckActive}
            onClick={dispatchTruck}
          >
            {truckActive ? 'Truck en route' : 'Dispatch truck'}
          </button>

          <button
            className={
              showFlow
                ? 'secondary twin-toggle active'
                : 'secondary twin-toggle'
            }
            onClick={() => setShowFlow((value) => !value)}
          >
            {showFlow ? 'Hide waste flow' : 'Show waste flow'}
          </button>

          <label
            className="secondary twin-toggle"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            Flow category
            <select
              value={flowCategory}
              onChange={(event) => setFlowCategory(event.target.value)}
            >
              <option>Plastic</option>
              <option>Organic</option>
              <option>Paper</option>
              <option>Metal</option>
              <option>Other</option>
            </select>
          </label>

          <button
            className="secondary"
            onClick={() => controls.current?.reset()}
          >
            Reset view
          </button>
        </div>
      </div>

      {showFlow && (
        <div className="twin-flow-note">
          {flowNote || 'Loading waste flow…'}
          {flowStages.length > 0 && (
            <span> Path: {flowStages.join(' → ')}</span>
          )}
        </div>
      )}

      {collectionSummary && (
        <div className="twin-collection-modal" role="dialog" aria-modal="true">
          <div className="twin-collection-modal-card">
            <div className="twin-collection-modal-icon">✓</div>
            <span className="eyebrow green-text">COLLECTION COMPLETE</span>
            <h3>Truck returned to the wastage yard</h3>
            <p>
              {collectionSummary.count} bin
              {collectionSummary.count === 1 ? '' : 's'} collected ·{' '}
              {collectionSummary.kilograms.toFixed(1)} kg transferred.
            </p>
            <div className="twin-collection-flow">
              <span>collection</span>
              <b>→</b>
              <span>sorting</span>
              {collectionSummary.destinations.map((destination) => (
                <Fragment key={destination}>
                  <b>→</b>
                  <span>{destination}</span>
                </Fragment>
              ))}
            </div>
            <small>
              The exact destination depends on each bin&apos;s configured waste
              stream. The recorded collection remains available in the backend.
            </small>
            <button
              className="primary"
              onClick={() => setCollectionSummary(null)}
            >
              Continue monitoring
            </button>
          </div>
        </div>
      )}

      {/* Interactive 3D campus */}
      <div
        style={{
          height: 'min(72vh, 760px)',
          minHeight: 460,
          width: '100%',
          overflow: 'hidden',
          borderRadius: 14,
          background: '#a9c6d5',
        }}
      >
        <Canvas
          dpr={[1, 1.5]}
          shadows
          frameloop="demand"
          gl={{
            antialias: true,
            powerPreference: 'high-performance',
            alpha: false,
          }}
          onCreated={({ gl }) => {
            gl.toneMapping = THREE.ACESFilmicToneMapping;
            gl.toneMappingExposure = 1.05;
          }}
        >
          <RendererLifecycle />

          <PerspectiveCamera
            makeDefault
            position={[260, 210, 300]}
            fov={50}
            near={1}
            far={1600}
          />

          <OrbitControls
            ref={controls}
            enablePan
            enableZoom
            enableRotate
            makeDefault
            minDistance={35}
            maxDistance={720}
            maxPolarAngle={Math.PI / 2.02}
            target={[0, 0, 0]}
            dampingFactor={0.08}
            enableDamping
          />

          <Scene
            bins={bins}
            selected={selected}
            onSelect={onSelect}
            recommendedBinId={recommendedBinId}
            flowStages={showFlow ? flowStages : []}
            flowCategory={flowCategory}
            showBuildings={showBuildings}
            showRoute={showRoute}
            truckRoute={truckRoute}
            truckProgress={truckProgress}
            truckActive={truckActive}
          />
        </Canvas>
      </div>

      <div className="twin-legend">
        <span>
          <i className="dot green" /> LOW / MEDIUM
        </span>
        <span>
          <i className="dot amber" /> HIGH
        </span>
        <span>
          <i className="dot orange" /> VERY HIGH
        </span>
        <span>
          <i className="dot red" /> CRITICAL
        </span>
        <span>
          Drag to orbit · scroll to zoom · click a bin for its backend state
        </span>
      </div>
    </div>
  );
}