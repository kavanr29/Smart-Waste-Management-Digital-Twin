'use client';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { ContactShadows, Html, Line, OrbitControls, PerspectiveCamera, RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import { api } from '../lib/api';

export type TwinBin = { id: number; code: string; name: string; zone: string; target_stream?: string; fill_pct: number; fill_kg: number; capacity_kg: number; status: string; contamination: number; accumulation_rate: number; overflow_hours: number | null; overflow_risk: number | null; priority_score: number; priority_label: string; priority_reasons: string[]; last_collection?: string };
type ZoneTelemetry = { power_kw: number; occupancy_count: number };
type CampusTelemetry = { zone_id: string; name: string; type: string; status: string; telemetry?: ZoneTelemetry };
type Vec3 = [number, number, number];
type BuildingSpec = { center: Vec3; name: string; width: number; depth: number; height: number; glass?: boolean; accent: string };
const BUILDINGS: BuildingSpec[] = [
    { center: [-10, 0, -7], name: 'Entrance & Security', width: 4.2, depth: 3.2, height: 2.6, accent: '#c7b69d' },
    { center: [-4, 0, -8], name: 'School of Engineering', width: 6.2, depth: 3.6, height: 2.4, glass: true, accent: '#d7d1c1' },
    { center: [4, 0, -8], name: 'Central Library & Auditorium', width: 5.2, depth: 3.4, height: 2.1, glass: true, accent: '#b8c2bd' },
    { center: [10, 0, -6], name: "S' Residences", width: 4.4, depth: 3.4, height: 3.2, accent: '#d4bfaa' },
    { center: [-10, 0, 6], name: 'CDSIMER Hospital', width: 5.5, depth: 3.6, height: 2.8, glass: true, accent: '#c6c9bd' },
    { center: [-3, 0, 8], name: 'Mess & Eateries', width: 4.5, depth: 3.1, height: 1.7, glass: true, accent: '#cdb58d' },
    { center: [7, 0, 7], name: 'Multi-Sports Facility', width: 5.0, depth: 3.2, height: 2.5, glass: true, accent: '#c3c9c3' },
];
const ZONE_POS: Record<string, Vec3> = { 'academic zone': [-5, 0, -8], 'hostel zone': [10, 0, -6], 'cafeteria zone': [-3, 0, 8], 'sports zone': [11, 0, 10], 'parking zone': [-14, 0, 1], 'administration zone': [-10, 0, -7], 'library zone': [4, 0, -8], 'garden / outdoor zone': [14, 0, -1], 'central plaza': [0, 0, 2], 'block a': [-5, 0, -8], 'block b': [4, 0, -8], 'block c': [-3, 0, 8], 'unassigned': [0, 0, 2] };
const FLOW_POS: Record<string, Vec3> = { collection: [0, .3, 0], sorting: [3, .3, -1], recycling: [6, .3, -3], recovery: [4, .3, 3], disposal: [-3, .3, 4] };
const STATUS_COLOR: Record<string, string> = { EMPTY: '#6f9b81', LOW: '#77c6a0', MEDIUM: '#f2c875', HIGH: '#f29b50', 'VERY HIGH': '#f07d5f', CRITICAL: '#ff817c' };
function zoneKey(zone: string) { const k = (zone || 'Unassigned').trim().toLowerCase(); return k === 'block a' ? 'academic zone' : k === 'block b' ? 'library zone' : k === 'block c' ? 'cafeteria zone' : k }
function binPosition(bin: TwinBin, bins: TwinBin[]): Vec3 { const key = zoneKey(bin.zone); const group = bins.filter(x => zoneKey(x.zone) === key); const i = Math.max(0, group.findIndex(x => x.id === bin.id)); const center = ZONE_POS[key] || ZONE_POS.unassigned; return [center[0] + (i % 3 - 1) * 1.05, .04, center[2] + 2 + (Math.floor(i / 3)) * 1.05] }
function Label({ children, className = '' }: { children: React.ReactNode; className?: string }) { return <div className={`twin-label ${className}`}>{children}</div> }
function RendererLifecycle() { const { gl, invalidate } = useThree(); useEffect(() => { gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.25)); const c = gl.domElement; const lost = (e: Event) => e.preventDefault(); const restored = () => invalidate(); c.addEventListener('webglcontextlost', lost); c.addEventListener('webglcontextrestored', restored); return () => { c.removeEventListener('webglcontextlost', lost); c.removeEventListener('webglcontextrestored', restored) } }, [gl, invalidate]); return null }
function GlassPanels({ w, d, h }: { w: number; d: number; h: number }) { const panels = Array.from({ length: Math.max(3, Math.floor(w / .75)) }); return <>{panels.map((_, i) => <group key={i}><mesh position={[-w / 2 + .38 + i * .75, h * .55, d / 2 + .025]}><boxGeometry args={[.58, h * .7, .035]} /><meshStandardMaterial color="#79a9b0" metalness={.35} roughness={.2} transparent opacity={.74} /></mesh><mesh position={[-w / 2 + .38 + i * .75, h * .55, d / 2 + .05]}><boxGeometry args={[.035, h * .74, .06]} /><meshStandardMaterial color="#9aa79b" metalness={.7} roughness={.28} /></mesh></group>)}</> }
const Building = memo(function Building({ spec }: { spec: BuildingSpec }) {
    const [x, _, z] = spec.center;
    const { width: w, depth: d, height: h } = spec;
    const floors = Math.max(2, Math.round(h / 0.9));
    const windowColor = spec.glass ? '#83b9c5' : '#567d83';
    const windows = [];
    const frontCols = Math.max(3, Math.floor(w / 0.72));
    for (let floor = 0; floor < floors; floor++) {
        for (let col = 0; col < frontCols; col++) {
            const wx = -w / 2 + (col + 0.5) * w / frontCols;
            const wy = 0.42 + floor * (h - 0.45) / floors;
            windows.push(
                <group key={`front-${floor}-${col}`}>
                    <mesh position={[wx, wy, d / 2 + 0.025]} castShadow>
                        <boxGeometry args={[w / frontCols * 0.64, 0.32, 0.045]} />
                        <meshPhysicalMaterial color={windowColor} metalness={0.35} roughness={0.18} clearcoat={0.8} />
                    </mesh>
                    <mesh position={[wx, wy, d / 2 + 0.052]}>
                        <boxGeometry args={[0.025, 0.36, 0.018]} />
                        <meshStandardMaterial color="#d6d9d0" metalness={0.45} roughness={0.3} />
                    </mesh>
                    <mesh position={[wx, wy - 0.19, d / 2 + 0.055]}>
                        <boxGeometry args={[w / frontCols * 0.72, 0.035, 0.09]} />
                        <meshStandardMaterial color="#9b9e91" roughness={0.68} />
                    </mesh>
                </group>
            );
        }
    }
    const sideCols = Math.max(2, Math.floor(d / 0.8));
    return <group position={[x, 0, z]}>
        {/* Main facade and a slightly raised stone foundation */}
        <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
            <boxGeometry args={[w, h, d]} />
            <meshStandardMaterial color={spec.accent} roughness={0.82} />
        </mesh>
        <mesh position={[0, 0.12, 0]} castShadow>
            <boxGeometry args={[w + 0.18, 0.24, d + 0.18]} />
            <meshStandardMaterial color="#8d9489" roughness={0.9} />
        </mesh>
        {windows}
        {/* Side elevation windows */}
        {Array.from({ length: floors * sideCols }).map((_, i) => {
            const floor = Math.floor(i / sideCols);
            const col = i % sideCols;
            const wy = 0.42 + floor * (h - 0.45) / floors;
            const wz = -d / 2 + (col + 0.5) * d / sideCols;
            return <group key={`side-${i}`}>
                <mesh position={[w / 2 + 0.025, wy, wz]} castShadow>
                    <boxGeometry args={[0.045, 0.32, d / sideCols * 0.62]} />
                    <meshPhysicalMaterial color={windowColor} metalness={0.32} roughness={0.2} clearcoat={0.65} />
                </mesh>
                <mesh position={[-w / 2 - 0.025, wy, wz]} castShadow>
                    <boxGeometry args={[0.045, 0.32, d / sideCols * 0.62]} />
                    <meshPhysicalMaterial color={windowColor} metalness={0.32} roughness={0.2} clearcoat={0.65} />
                </mesh>
            </group>;
        })}
        {/* Roof slab, parapet and small rooftop service units */}
        <mesh position={[0, h + 0.08, 0]} castShadow>
            <boxGeometry args={[w + 0.28, 0.16, d + 0.28]} />
            <meshStandardMaterial color="#8f968b" roughness={0.72} />
        </mesh>
        <mesh position={[0, h + 0.2, -d / 2 + 0.08]}>
            <boxGeometry args={[w + 0.08, 0.22, 0.12]} />
            <meshStandardMaterial color="#c6c9bd" roughness={0.78} />
        </mesh>