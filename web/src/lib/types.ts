export type User = {
  id: string;
  name: string;
  email: string;
};

export type Project = {
  id: string;
  name: string;
  created_at?: string;
};

export type DiscoveryRun = {
  id: string;
  name: string | null;
  status: string;
  max_domains?: number;
  log?: string;
  summary?: string;
  error?: string;
  saved?: number;
  complete?: boolean;
  lead_domains?: string[];
  rejected_domains?: string[];
  stats?: Record<string, unknown>;
};

export type Lead = {
  id: string;
  domain: string;
  store_name?: string | null;
  public_email?: string | null;
  country?: string | null;
  freshness_level?: string | null;
  freshness_score?: number | null;
  source?: string | null;
};

export type Rejected = {
  id: string;
  domain: string;
  store_name?: string | null;
  public_email?: string | null;
  reason?: string | null;
};
