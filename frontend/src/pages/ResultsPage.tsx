import React, { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Report, Interaction } from "../api/client";
import DrugGraph from "../components/DrugGraph";

const RISK_BANNER: Record<string, { bg: string; text: string; label: string }> = {
  major:    { bg: "bg-red-100",    text: "text-red-700",    label: "Major Risk" },
  moderate: { bg: "bg-orange-100", text: "text-orange-700", label: "Moderate Risk" },
  minor:    { bg: "bg-yellow-100", text: "text-yellow-700", label: "Minor Risk" },
  unknown:  { bg: "bg-gray-100",   text: "text-gray-700",   label: "Unknown Severity" },
  none:     { bg: "bg-green-100",  text: "text-green-700",  label: "No Known Interactions" },
};

const SEVERITY_BADGE: Record<string, string> = {
  major:    "bg-red-100 text-red-700",
  moderate: "bg-orange-100 text-orange-700",
  minor:    "bg-yellow-100 text-yellow-700",
  unknown:  "bg-gray-100 text-gray-600",
};

function InteractionCard({ interaction }: { interaction: Interaction }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-medium text-gray-900">
          {interaction.drug_a} + {interaction.drug_b}
        </span>
        <span
          className={`text-xs px-2 py-1 rounded-full font-medium ${
            SEVERITY_BADGE[interaction.severity] || SEVERITY_BADGE.unknown
          }`}
        >
          {interaction.severity}
        </span>
      </div>
      <p className="text-sm text-gray-600">{interaction.description}</p>
      {interaction.source_id && (
        <p className="text-xs text-gray-400">
          Source:{" "}
          <a
            href={`https://api.fda.gov/drug/label.json?search=set_id:${interaction.source_id}`}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-blue-500"
          >
            FDA label {interaction.source_id.slice(0, 8)}...
          </a>
        </p>
      )}
    </div>
  );
}

export default function ResultsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const report = location.state?.report as Report | undefined;
  const [graphDrug, setGraphDrug] = useState<string | null>(null);

  if (!report) {
    navigate("/");
    return null;
  }

  const banner = RISK_BANNER[report.risk_level] || RISK_BANNER.unknown;

  // Collect unique drug names from interactions for graph exploration
  const drugNames = Array.from(
    new Set(report.interactions.flatMap((i) => [i.drug_a, i.drug_b]))
  );

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-2xl mx-auto space-y-6">

        {/* Back button */}
        <button
          onClick={() => navigate("/")}
          className="text-sm text-blue-600 hover:underline"
        >
          ← New query
        </button>

        {/* Risk banner */}
        <div className={`rounded-lg p-4 ${banner.bg}`}>
          <p className={`font-semibold ${banner.text}`}>{banner.label}</p>
          <p className={`text-sm mt-1 ${banner.text}`}>{report.summary}</p>
        </div>

        {/* Interaction cards */}
        {report.interactions.length > 0 && (
          <div className="space-y-3">
            <h2 className="font-semibold text-gray-800">Interactions</h2>
            {report.interactions.map((i, idx) => (
              <InteractionCard key={idx} interaction={i} />
            ))}
          </div>
        )}

        {/* Contraindications */}
        {report.contraindications.length > 0 && (
          <div className="space-y-2">
            <h2 className="font-semibold text-gray-800">Contraindications</h2>
            {report.contraindications.map((c, idx) => (
              <div key={idx} className="border border-red-200 rounded-lg p-3 text-sm">
                <span className="font-medium">{c.drug}</span> —{" "}
                <span className="text-red-600">{c.risk_type}</span> with{" "}
                <span className="font-medium">{c.condition}</span>
              </div>
            ))}
          </div>
        )}

        {/* Unrecognized drugs */}
        {report.unrecognized_drugs.length > 0 && (
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-sm text-yellow-800">
            <strong>Not recognized:</strong> {report.unrecognized_drugs.join(", ")} — these
            drugs could not be found in our database and were not checked.
          </div>
        )}

        {/* Drug graph */}
        {drugNames.length > 0 && (
          <div className="space-y-3">
            <h2 className="font-semibold text-gray-800">Drug Relationship Graph</h2>
            <div className="flex flex-wrap gap-2">
              {drugNames.map((name) => (
                <button
                  key={name}
                  onClick={() => setGraphDrug(name === graphDrug ? null : name)}
                  className={`text-xs px-3 py-1 rounded-full border ${
                    graphDrug === name
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-gray-600 border-gray-300 hover:border-blue-400"
                  }`}
                >
                  {name}
                </button>
              ))}
            </div>
            {graphDrug && <DrugGraph drugId={graphDrug} />}
          </div>
        )}

        {/* Disclaimer */}
        <p className="text-xs text-gray-400 border-t pt-4">{report.disclaimer}</p>
      </div>
    </div>
  );
}
