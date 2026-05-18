/**
 * D3 force-directed dependency graph.
 *
 * Two-effect split so polling-driven prop changes don't blow away the
 * graph every 3 s:
 *
 *   - mount effect (no deps): creates the SVG <defs>, the link <g>,
 *     the node <g>, and the d3 force simulation. Stores everything
 *     in refs. Runs once.
 *   - data effect (deps: pods, edges, selectedShort): rebinds data
 *     via a keyed join, transitions fill/stroke changes (250 ms
 *     ease-out), fade-in new nodes/links (400 ms), fade-out removed.
 *
 * Anomaly emphasis: critical pods get the red SVG drop-shadow filter,
 * warn pods get the amber fill but no glow.
 *
 * Stable positioning: forceX/forceY toward centre at very low strength
 * prevents drift. Every tick clamps positions inside the padded rect
 * so glows never get clipped.
 */

import * as d3 from 'd3';
import { useEffect, useRef } from 'react';
import type { FlowEdge, PodRow, Severity } from '../types';

interface Props {
  pods: PodRow[];
  edges: FlowEdge[];
  selectedShort: string | null;
  onSelect(short: string | null): void;
}

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  namespace: string;
  anomaly: Severity | null;
  degree: number;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  count: number;
}

const NAMESPACE_STROKE: Record<string, string> = {
  'sh-core': '#94A3B8', // slate-400
  'sh-edge': '#818CF8', // indigo-400
  'sh-ops':  '#34D399', // emerald-400
  // Darker neutral so the control-plane namespace is visibly
  // "infrastructure" and we don't share the amber that warn-level
  // anomalies use for their fill.
  'podmind': '#475569', // slate-600
  default:   '#CBD5E1', // slate-300
};

export function DependencyGraph({ pods, edges, selectedShort, onSelect }: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simRef = useRef<d3.Simulation<SimNode, SimLink> | null>(null);
  const linkGRef = useRef<SVGGElement | null>(null);
  const nodeGRef = useRef<SVGGElement | null>(null);
  // Node positions keyed by id so reused nodes don't snap on re-bind.
  const positionsRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  // Track the latest onSelect via a ref so the mount effect doesn't
  // need it in its dep array.
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  // ----- mount effect (one-shot) -----
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const W = rect.width;
    const H = rect.height;
    const PAD = 28;

    const root = d3.select(svg);
    // Reset in case of strict-mode double-mount.
    root.selectAll('*').remove();

    // defs: red glow filter
    const defs = root.append('defs');
    const filter = defs
      .append('filter')
      .attr('id', 'glow-red')
      .attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%');
    filter.append('feGaussianBlur').attr('stdDeviation', 4).attr('result', 'b');
    filter
      .append('feFlood').attr('flood-color', '#E5484D').attr('flood-opacity', '0.55').attr('result', 'c');
    filter.append('feComposite').attr('in', 'c').attr('in2', 'b').attr('operator', 'in').attr('result', 'g');
    const merge = filter.append('feMerge');
    merge.append('feMergeNode').attr('in', 'g');
    merge.append('feMergeNode').attr('in', 'SourceGraphic');

    const linkG = root.append('g').attr('stroke', '#D4D4D4').attr('stroke-opacity', 0.9);
    const nodeG = root.append('g');
    linkGRef.current = linkG.node();
    nodeGRef.current = nodeG.node();

    const simulation = d3
      .forceSimulation<SimNode>([])
      .force(
        'link',
        d3.forceLink<SimNode, SimLink>([]).id((d) => d.id).distance(90).strength(0.55),
      )
      .force('charge', d3.forceManyBody<SimNode>().strength(-320))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('x', d3.forceX(W / 2).strength(0.05))
      .force('y', d3.forceY(H / 2).strength(0.05))
      .force(
        'collide',
        d3.forceCollide<SimNode>().radius((d) => 18 + Math.min(7, d.degree * 1.6)),
      )
      .on('tick', () => {
        const nodes = simulation.nodes();
        for (const n of nodes) {
          if (n.x! < PAD) n.x = PAD;
          else if (n.x! > W - PAD) n.x = W - PAD;
          if (n.y! < PAD) n.y = PAD;
          else if (n.y! > H - PAD) n.y = H - PAD;
          positionsRef.current.set(n.id, { x: n.x!, y: n.y! });
        }
        d3.select(linkGRef.current)
          .selectAll<SVGLineElement, SimLink>('line')
          .attr('x1', (d) => (d.source as SimNode).x!)
          .attr('y1', (d) => (d.source as SimNode).y!)
          .attr('x2', (d) => (d.target as SimNode).x!)
          .attr('y2', (d) => (d.target as SimNode).y!);
        d3.select(nodeGRef.current)
          .selectAll<SVGGElement, SimNode>('g.node')
          .attr('transform', (d) => `translate(${d.x},${d.y})`);
      });
    simRef.current = simulation;

    return () => {
      simulation.stop();
      simRef.current = null;
    };
  }, []);

  // ----- data effect -----
  useEffect(() => {
    const simulation = simRef.current;
    const linkGNode = linkGRef.current;
    const nodeGNode = nodeGRef.current;
    if (!simulation || !linkGNode || !nodeGNode) return;

    // Build SimNode array. Reuse existing positions for stable layout.
    const degreeByPod = new Map<string, number>();
    for (const e of edges) {
      degreeByPod.set(e.source, (degreeByPod.get(e.source) ?? 0) + 1);
      degreeByPod.set(e.target, (degreeByPod.get(e.target) ?? 0) + 1);
    }
    // Look up existing nodes so we preserve x/y/vx/vy.
    const existing = new Map<string, SimNode>();
    for (const n of simulation.nodes()) existing.set(n.id, n);
    const nodes: SimNode[] = pods.map((p) => {
      const prev = existing.get(p.short);
      if (prev) {
        prev.namespace = p.namespace;
        prev.anomaly = p.anomaly;
        prev.degree = degreeByPod.get(p.short) ?? 0;
        return prev;
      }
      const pos = positionsRef.current.get(p.short);
      return {
        id: p.short,
        namespace: p.namespace,
        anomaly: p.anomaly,
        degree: degreeByPod.get(p.short) ?? 0,
        ...(pos ?? {}),
      };
    });

    const links: SimLink[] = edges
      // Drop edges that point at pods we don't have (race conditions
      // where a flow row mentions a pod we just filtered out).
      .filter((e) => nodes.find((n) => n.id === e.source) && nodes.find((n) => n.id === e.target))
      .map((e) => ({ source: e.source, target: e.target, count: e.count }));

    simulation.nodes(nodes);
    const linkForce = simulation.force<d3.ForceLink<SimNode, SimLink>>('link');
    if (linkForce) linkForce.links(links);
    simulation.alpha(0.4).restart();

    // ----- link binding -----
    const linkSel = d3
      .select(linkGNode)
      .selectAll<SVGLineElement, SimLink>('line')
      .data(
        links,
        (d: SimLink) =>
          `${(d.source as SimNode).id ?? d.source}->${(d.target as SimNode).id ?? d.target}`,
      )
      .join(
        (enter) =>
          enter
            .append('line')
            .style('opacity', 0)
            .attr('stroke-width', (d) => Math.min(2.5, 0.6 + d.count * 0.04))
            .call((s) => s.transition().duration(400).style('opacity', 1)),
        (update) =>
          update.attr('stroke-width', (d) => Math.min(2.5, 0.6 + d.count * 0.04)),
        (exit) => exit.call((s) => s.transition().duration(250).style('opacity', 0).remove()),
      );
    void linkSel; // referenced via the selection above

    // ----- node binding -----
    const nodeSel = d3
      .select(nodeGNode)
      .selectAll<SVGGElement, SimNode>('g.node')
      .data(nodes, (d) => d.id)
      .join(
        (enter) => {
          const g = enter
            .append('g')
            .attr('class', 'node')
            .style('cursor', 'pointer')
            .style('opacity', 0)
            .on('click', (event, d) => {
              event.stopPropagation();
              onSelectRef.current(d.id);
            });
          g.append('circle');
          g.append('text')
            .attr('text-anchor', 'middle')
            .attr('font-family', '"JetBrains Mono", ui-monospace, monospace')
            .attr('font-size', 10);
          g.transition().duration(400).style('opacity', 1);
          // Apply drag here; setup is mount-stable.
          g.call(
            d3
              .drag<SVGGElement, SimNode>()
              .on('start', (event, d) => {
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
              })
              .on('drag', (event, d) => {
                d.fx = event.x;
                d.fy = event.y;
              })
              .on('end', (event, d) => {
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
              }),
          );
          return g;
        },
        (update) => update,
        (exit) => exit.call((g) => g.transition().duration(250).style('opacity', 0).remove()),
      );

    const fillFor = (d: SimNode): string => {
      if (d.anomaly === 'critical') return '#E5484D';
      if (d.anomaly === 'warn') return '#F59E0B';
      return '#FFFFFF';
    };
    const strokeFor = (d: SimNode): string =>
      NAMESPACE_STROKE[d.namespace] ?? NAMESPACE_STROKE.default;

    nodeSel
      .select<SVGCircleElement>('circle')
      .attr('r', (d) => 10 + Math.min(7, d.degree * 1.6))
      .attr('filter', (d) => (d.anomaly === 'critical' ? 'url(#glow-red)' : null))
      .transition()
      .duration(250)
      .ease(d3.easeCubicOut)
      .attr('fill', fillFor)
      .attr('stroke', strokeFor)
      .attr('stroke-width', (d) => (selectedShort === d.id ? 3 : 2));

    nodeSel
      .select<SVGTextElement>('text')
      .text((d) => d.id)
      .attr('y', (d) => 10 + Math.min(7, d.degree * 1.6) + 12)
      .transition()
      .duration(250)
      .attr('fill', (d) => (d.anomaly ? '#E5484D' : '#3A3A3A'));
  }, [pods, edges, selectedShort]);

  return (
    <div className="card h-full flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle flex items-baseline gap-2">
        <div className="text-[10px] font-mono uppercase tracking-widest text-ink-400">
          Dependency Graph
        </div>
        <div className="font-mono text-[12px] text-ink-700">
          {pods.length} pods · {edges.length} edges
        </div>
        <Legend />
      </div>
      <div className="flex-1 min-h-0 relative">
        <svg
          ref={svgRef}
          className="absolute inset-0 w-full h-full"
          onClick={() => onSelect(null)}
        />
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="ml-auto flex items-center gap-3">
      {Object.entries(NAMESPACE_STROKE)
        .filter(([k]) => k !== 'default' && k !== 'podmind')
        .map(([ns, color]) => (
          <span key={ns} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2 w-2 rounded-full border-2"
              style={{ borderColor: color, background: '#FFF' }}
            />
            <span className="font-mono text-[10px] text-ink-500">{ns}</span>
          </span>
        ))}
    </div>
  );
}
