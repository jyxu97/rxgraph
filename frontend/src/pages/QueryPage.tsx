import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { queryDrugs, uploadImage, Report } from "../api/client";

const EXAMPLE_QUERIES = [
  "Can I take warfarin with aspirin?",
  "I have hypertension — is ibuprofen safe with my metoprolol?",
  "Is it safe to take sertraline and tramadol together?",
];

export default function QueryPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [conditions, setConditions] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() && !imageFile) return;

    setLoading(true);
    setError(null);

    try {
      let s3Key: string | undefined;
      if (imageFile) {
        s3Key = await uploadImage(imageFile);
      }

      const conditionList = conditions
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean);

      const report: Report = await queryDrugs(query, s3Key, conditionList);
      navigate("/results", { state: { report } });
    } catch (err: any) {
      setError(err.response?.data?.detail || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center p-6">
      <div className="w-full max-w-xl">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">RxGraph</h1>
        <p className="text-gray-500 mb-8">
          Check drug interactions and contraindications backed by FDA data.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Text input */}
          <textarea
            className="w-full border border-gray-300 rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            rows={3}
            placeholder="Type your medication names or question..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />

          {/* Image upload */}
          <div className="border-2 border-dashed border-gray-300 rounded-lg p-4 text-center text-sm text-gray-500">
            {imageFile ? (
              <div className="flex items-center justify-between">
                <span className="text-gray-700">{imageFile.name}</span>
                <button
                  type="button"
                  className="text-red-500 text-xs ml-2"
                  onClick={() => setImageFile(null)}
                >
                  Remove
                </button>
              </div>
            ) : (
              <label className="cursor-pointer">
                Upload prescription label or medication list
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => setImageFile(e.target.files?.[0] || null)}
                />
              </label>
            )}
          </div>

          {/* Optional conditions */}
          <input
            type="text"
            className="w-full border border-gray-300 rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Known conditions (optional, comma-separated): hypertension, diabetes"
            value={conditions}
            onChange={(e) => setConditions(e.target.value)}
          />

          {error && <p className="text-red-500 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={loading || (!query.trim() && !imageFile)}
            className="w-full bg-blue-600 text-white rounded-lg py-3 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Checking interactions..." : "Check Interactions"}
          </button>
        </form>

        {/* Example queries */}
        <div className="mt-6">
          <p className="text-xs text-gray-400 mb-2">Try an example:</p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLE_QUERIES.map((q) => (
              <button
                key={q}
                onClick={() => setQuery(q)}
                className="text-xs bg-white border border-gray-200 rounded-full px-3 py-1 text-gray-600 hover:border-blue-400 hover:text-blue-600"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
