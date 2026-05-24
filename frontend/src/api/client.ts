import axios from "axios";

const api = axios.create({
  baseURL: "http://localhost:8000",
});

export interface Interaction {
  drug_a: string;
  drug_b: string;
  severity: "major" | "moderate" | "minor" | "unknown";
  description: string;
  source_id: string;
}

export interface Contraindication {
  drug: string;
  condition: string;
  risk_type: string;
}

export interface Report {
  risk_level: string;
  summary: string;
  interactions: Interaction[];
  contraindications: Contraindication[];
  unrecognized_drugs: string[];
  sources: string[];
  disclaimer: string;
}

export interface GraphNode {
  id: string;
  label: string;
  drug_class: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  severity: string;
  description: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export const queryDrugs = async (
  query: string,
  imagS3Key?: string,
  conditions: string[] = []
): Promise<Report> => {
  const { data } = await api.post<Report>("/api/query", {
    query,
    image_s3_key: imagS3Key,
    conditions,
  });
  return data;
};

export const uploadImage = async (file: File): Promise<string> => {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await api.post<{ s3_key: string }>(
    "/api/upload-image",
    formData
  );
  return data.s3_key;
};

export const getGraph = async (drugId: string): Promise<GraphData> => {
  const { data } = await api.get<GraphData>(`/api/graph/${drugId}`);
  return data;
};
