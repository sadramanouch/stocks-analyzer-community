export type CandidateView = "purchases" | "financial" | "watched" | "all";

export type ResearchCandidate = {
  ticker: string;
  company_name: string;
  state: string;
  fresh_purchases: number;
  financial_gate_reasons: string[];
  watched: boolean;
};

export function qualifiesForPurchaseReview(candidate: ResearchCandidate): boolean {
  return candidate.state === "fresh_purchase_and_documented_inputs"
    && candidate.financial_gate_reasons.length === 0
    && Number.isInteger(candidate.fresh_purchases)
    && candidate.fresh_purchases > 0;
}

export function filterResearchCandidates<T extends ResearchCandidate>(candidates: T[], view: CandidateView, search = ""): T[] {
  const query = search.trim().toLowerCase();
  return candidates.filter(candidate => {
    const matches = view === "purchases" ? qualifiesForPurchaseReview(candidate)
      : view === "financial" ? candidate.financial_gate_reasons.length === 0
      : view === "watched" ? candidate.watched : true;
    return matches && (!query || `${candidate.ticker} ${candidate.company_name}`.toLowerCase().includes(query));
  });
}
