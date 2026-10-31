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

export type TrackingTech = {
  name: string;
  confidence: "confirmed" | "uncertain" | string;
};

export type Lead = {
  id: string;
  domain: string;
  store_name?: string | null;
  public_email?: string | null;
  country?: string | null;
  freshness_level?: string | null;
  freshness_score?: number | null;
  freshness_evidence?: string | null;
  source?: string | null;
  theme_name?: string | null;
  theme_confidence?: string | null;
  tracking_technologies?: TrackingTech[];
  first_seen?: string | null;
  last_checked?: string | null;
};

export type Rejected = {
  id: string;
  domain: string;
  store_name?: string | null;
  public_email?: string | null;
  reason?: string | null;
};

export type StoreProduct = {
  id: string;
  domain: string;
  shopify_product_id?: string;
  title: string;
  handle?: string;
  product_url?: string;
  price?: string;
  currency?: string;
  image_url?: string;
  category?: string;
};

export type WatchlistItem = {
  id: string;
  domain: string;
  note?: string;
  created_at?: string;
  updated_at?: string;
  lead?: Lead | null;
  product_count?: number;
};

export type SavedFilter = {
  id: string;
  name: string;
  config: {
    max_domains?: number;
    target_countries?: string[];
    keep_unknown_country?: boolean;
    store_age_filter?: "all" | "new" | "established" | "unknown";
    theme_filter?: string;
    tracking_filter?: string;
    all_countries?: boolean;
    [key: string]: unknown;
  };
  created_at?: string;
  updated_at?: string;
};

export type StoreCompareResult = {
  a: {
    domain: string;
    store_name?: string;
    country?: string;
    freshness_level?: string;
    freshness_score?: number | null;
    freshness_evidence?: string;
    theme_name?: string;
    theme_confidence?: string;
    tracking_technologies?: TrackingTech[];
    first_seen?: string;
    last_checked?: string;
    product_count?: number;
  };
  b: {
    domain: string;
    store_name?: string;
    country?: string;
    freshness_level?: string;
    freshness_score?: number | null;
    freshness_evidence?: string;
    theme_name?: string;
    theme_confidence?: string;
    tracking_technologies?: TrackingTech[];
    first_seen?: string;
    last_checked?: string;
    product_count?: number;
  };
  fields: {
    country_same?: boolean;
    theme_same?: boolean;
    freshness_level_same?: boolean;
    tracking_shared?: string[];
    tracking_only_a?: string[];
    tracking_only_b?: string[];
  };
  products: {
    shared: Array<{
      match_by: string;
      match_key: string;
      a: StoreProduct;
      b: StoreProduct;
    }>;
    only_a: StoreProduct[];
    only_b: StoreProduct[];
    shared_count: number;
    only_a_count: number;
    only_b_count: number;
  };
};
