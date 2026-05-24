import React, { useEffect, useState } from "react";
import {
  ReactFlow,
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getGraph } from "../api/client";

const SEVERITY_COLORS: Record<string, string> = {
  major: "#ef4444",
  moderate: "#f97316",
  minor: "#eab308",
  unknown: "#9ca3af",
};

const NODE_COLOR = "#3b82f6";

interface Props {
  drugId: string;
}

export default function DrugGraph({ drugId }: Props) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getGraph(drugId)
      .then((data) => {
        // Position center drug in the middle, neighbors in a circle around it
        const centerNode = data.nodes.find((n) => n.id === drugId);
        const neighbors = data.nodes.filter((n) => n.id !== drugId);
        const radius = 220;

        const flowNodes: Node[] = [];

        if (centerNode) {
          flowNodes.push({
            id: centerNode.id,
            data: { label: centerNode.label },
            position: { x: 400, y: 300 },
            style: {
              background: NODE_COLOR,
              color: "#fff",
              border: "none",
              borderRadius: 8,
              fontWeight: 600,
              padding: "8px 14px",
            },
          });
        }

        neighbors.forEach((n, i) => {
          const angle = (2 * Math.PI * i) / neighbors.length;
          flowNodes.push({
            id: n.id,
            data: { label: n.label },
            position: {
              x: 400 + radius * Math.cos(angle),
              y: 300 + radius * Math.sin(angle),
            },
            style: {
              background: "#fff",
              border: "1px solid #e5e7eb",
              borderRadius: 8,
              padding: "6px 12px",
            },
          });
        });

        const flowEdges: Edge[] = data.edges.map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.severity,
          style: { stroke: SEVERITY_COLORS[e.severity] || "#9ca3af" },
          labelStyle: {
            fontSize: 10,
            fill: SEVERITY_COLORS[e.severity] || "#9ca3af",
          },
        }));

        setNodes(flowNodes);
        setEdges(flowEdges);
      })
      .catch(() => setError("Failed to load graph."))
      .finally(() => setLoading(false));
  }, [drugId]);

  if (loading) return <div className="text-sm text-gray-400 p-4">Loading graph...</div>;
  if (error) return <div className="text-sm text-red-400 p-4">{error}</div>;

  return (
    <div style={{ height: 500 }} className="border border-gray-200 rounded-lg overflow-hidden">
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  );
}
