"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  ApiError,
  addWatchlist,
  compareStores,
  createRun,
  createSavedFilter,
  deleteSavedFilter,
  downloadCsv,
  getRun,
  listLeads,
  listProducts,
  listProjects,
  listRejected,
  listRuns,
  listSavedFilters,
  listWatchlist,
  removeWatchlist,
} from "@/lib/api";
import { COUNTRY_CHOICES } from "@/lib/countries";
import type {
  DiscoveryRun,
  Lead,
  Project,
  Rejected,
  SavedFilter,
  StoreCompareResult,
  StoreProduct,
  WatchlistItem,
} from "@/lib/types";

type Tab = "runs" | "leads" | "products" | "rejected" | "research";

export default function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;

  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<DiscoveryRun | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [products, setProducts] = useState<StoreProduct[]>([]);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [savedFilters, setSavedFilters] = useState<SavedFilter[]>([]);
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [compareResult, setCompareResult] = useState<StoreCompareResult | null>(null);
  const [filterName, setFilterName] = useState("");
  const [query, setQuery] = useState("");
  const [themeFilter, setThemeFilter] = useState("all");
  const [trackingFilter, setTrackingFilter] = useState("all");
  const [productCategoryFilter, setProductCategoryFilter] = useState("all");
  const [productDomainFilter, setProductDomainFilter] = useState("all");
  const [maxDomains, setMaxDomains] = useState(20);
  const [allCountries, setAllCountries] = useState(true);
  const [selectedCountries, setSelectedCountries] = useState<string[]>(["US", "GB", "CA", "AU", "NG"]);
  const [keepUnknownCountry, setKeepUnknownCountry] = useState(false);
  const [storeAgeFilter, setStoreAgeFilter] = useState<"all" | "new" | "established" | "unknown">(
    "all",
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    // Keep initial load to five account-DB calls (2-session cap). Research
    // endpoints load when the Research tab opens.
    const [projects, runRows, leadRows, productRows, rejectedRows] = await Promise.all([
      listProjects(),
      listRuns(projectId),
      listLeads(projectId),
      listProducts(projectId),
      listRejected(projectId),
    ]);
    setProject(projects.find((item) => item.id === projectId) || null);
    setRuns(runRows);
    setLeads(leadRows);
    setProducts(productRows);
    setRejected(rejectedRows);
    if (runRows[0]) {
      const detail = await getRun(projectId, runRows[0].id);
      setSelectedRun(detail);
    } else {
      setSelectedRun(null);
    }
  }, [projectId]);

  useEffect(() => {
    load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Could not load project");
    });
  }, [load]);

  // Poll activity while a cycle is queued/running.
  useEffect(() => {
    if (!selectedRun || (selectedRun.status !== "queued" && selectedRun.status !== "running")) {
      return;
    }
    const runId = selectedRun.id;
    const timer = setInterval(() => {
      getRun(projectId, runId)
        .then((detail) => {
          setSelectedRun(detail);
          setRuns((current) =>
            current.map((run) => (run.id === detail.id ? { ...run, status: detail.status } : run)),
          );
        })
        .catch(() => undefined);
    }, 4000);
    return () => clearInterval(timer);
  }, [projectId, selectedRun?.id, selectedRun?.status]);

  // Worker copies qualifying rows into the account DB when the cycle ends.
  // Reload leads/rejected then; polling alone only refreshed the activity log.
  useEffect(() => {
    if (!selectedRun || selectedRun.status === "queued" || selectedRun.status === "running") {
      return;
    }
    let cancelled = false;
    Promise.all([
      listLeads(projectId),
      listProducts(projectId),
      listRejected(projectId),
      listRuns(projectId),
    ])
      .then(([leadRows, productRows, rejectedRows, runRows]) => {
        if (cancelled) return;
        setLeads(leadRows);
        setProducts(productRows);
        setRejected(rejectedRows);
        setRuns(runRows);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedRun?.id, selectedRun?.status]);

  const themeOptions = useMemo(() => {
    const names = new Set<string>();
    for (const lead of leads) {
      if (lead.theme_name) names.add(lead.theme_name);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [leads]);

  const trackingOptions = useMemo(() => {
    const names = new Set<string>();
    for (const lead of leads) {
      for (const tech of lead.tracking_technologies || []) {
        if (tech.name) names.add(tech.name);
      }
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [leads]);

  const filteredLeads = useMemo(() => {
    const q = query.trim().toLowerCase();
    return leads.filter((row) => {
      if (themeFilter === "undetected" && row.theme_name) return false;
      if (themeFilter !== "all" && themeFilter !== "undetected" && row.theme_name !== themeFilter) {
        return false;
      }
      if (trackingFilter === "none" && (row.tracking_technologies || []).length > 0) return false;
      if (
        trackingFilter !== "all" &&
        trackingFilter !== "none" &&
        !(row.tracking_technologies || []).some((tech) => tech.name === trackingFilter)
      ) {
        return false;
      }
      if (!q) return true;
      const trackingText = (row.tracking_technologies || [])
        .map((tech) => `${tech.name} ${tech.confidence}`)
        .join(" ");
      return [row.domain, row.store_name, row.public_email, row.country, row.source, row.theme_name, trackingText]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q));
    });
  }, [leads, query, themeFilter, trackingFilter]);

  const filteredRejected = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rejected;
    return rejected.filter((row) =>
      [row.domain, row.store_name, row.reason, row.public_email]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q)),
    );
  }, [rejected, query]);

  const productCategoryOptions = useMemo(() => {
    const names = new Set<string>();
    for (const product of products) {
      if (product.category) names.add(product.category);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [products]);

  const productDomainOptions = useMemo(() => {
    const names = new Set<string>();
    for (const product of products) {
      if (product.domain) names.add(product.domain);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [products]);

  const filteredProducts = useMemo(() => {
    const q = query.trim().toLowerCase();
    return products.filter((row) => {
      if (productDomainFilter !== "all" && row.domain !== productDomainFilter) return false;
      if (productCategoryFilter === "uncategorized" && row.category) return false;
      if (
        productCategoryFilter !== "all" &&
        productCategoryFilter !== "uncategorized" &&
        row.category !== productCategoryFilter
      ) {
        return false;
      }
      if (!q) return true;
      return [row.title, row.domain, row.category, row.price, row.currency, row.product_url]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q));
    });
  }, [products, query, productCategoryFilter, productDomainFilter]);

  const watchedDomains = useMemo(
    () => new Set(watchlist.map((item) => item.domain)),
    [watchlist],
  );

  function currentFilterConfig(): SavedFilter["config"] {
    return {
      max_domains: maxDomains,
      all_countries: allCountries,
      target_countries: allCountries ? [] : selectedCountries,
      keep_unknown_country: allCountries ? false : keepUnknownCountry,
      store_age_filter: storeAgeFilter,
      theme_filter: themeFilter,
      tracking_filter: trackingFilter,
    };
  }

  function applySavedFilter(item: SavedFilter) {
    const config = item.config || {};
    if (typeof config.max_domains === "number" && config.max_domains > 0) {
      setMaxDomains(config.max_domains);
    }
    if (typeof config.all_countries === "boolean") {
      setAllCountries(config.all_countries);
    } else if (Array.isArray(config.target_countries)) {
      setAllCountries(config.target_countries.length === 0);
    }
    if (Array.isArray(config.target_countries)) {
      setSelectedCountries(config.target_countries.map(String));
    }
    if (typeof config.keep_unknown_country === "boolean") {
      setKeepUnknownCountry(config.keep_unknown_country);
    }
    if (
      config.store_age_filter === "all" ||
      config.store_age_filter === "new" ||
      config.store_age_filter === "established" ||
      config.store_age_filter === "unknown"
    ) {
      setStoreAgeFilter(config.store_age_filter);
    }
    if (typeof config.theme_filter === "string") setThemeFilter(config.theme_filter);
    if (typeof config.tracking_filter === "string") setTrackingFilter(config.tracking_filter);
    setTab("runs");
  }

  async function onToggleWatch(domain: string) {
    setError("");
    try {
      const existing = watchlist.find((item) => item.domain === domain);
      if (existing) {
        await removeWatchlist(projectId, existing.id);
        setWatchlist((rows) => rows.filter((item) => item.id !== existing.id));
      } else {
        const row = await addWatchlist(projectId, domain);
        setWatchlist((rows) => [row, ...rows.filter((item) => item.domain !== domain)]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update watchlist");
    }
  }

  async function onSaveFilter(event: FormEvent) {
    event.preventDefault();
    const name = filterName.trim();
    if (!name) {
      setError("Name the filter configuration before saving.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const row = await createSavedFilter(projectId, name, currentFilterConfig());
      setSavedFilters((rows) => [row, ...rows]);
      setFilterName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save filter");
    } finally {
      setBusy(false);
    }
  }

  async function onCompare(event: FormEvent) {
    event.preventDefault();
    if (!compareA || !compareB || compareA === compareB) {
      setError("Pick two different saved stores to compare.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await compareStores(projectId, compareA, compareB);
      setCompareResult(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not compare stores");
      setCompareResult(null);
    } finally {
      setBusy(false);
    }
  }

  async function onStartRun(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (!allCountries && selectedCountries.length === 0) {
        setError("Select at least one country, or enable All countries.");
        setBusy(false);
        return;
      }
      const targetCountries = allCountries ? [] : selectedCountries;
      const run = await createRun(projectId, {
        name: `Discovery ${new Date().toLocaleString()}`,
        max_domains: maxDomains,
        on_page: false,
        settings: {
          max_domains: maxDomains,
          target_countries: targetCountries,
          keep_unknown_country: allCountries ? false : keepUnknownCountry,
          store_age_filter: storeAgeFilter,
        },
      });
      await load();
      const detail = await getRun(projectId, run.id);
      setSelectedRun(detail);
      setTab("runs");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start run");
    } finally {
      setBusy(false);
    }
  }

  function toggleCountry(code: string) {
    setSelectedCountries((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code],
    );
  }

  async function openRun(runId: string) {
    try {
      const detail = await getRun(projectId, runId);
      setSelectedRun(detail);
      setTab("runs");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load run");
    }
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{project?.name || "Project"}</h1>
          <p>Queue discovery runs and review leads for this project.</p>
        </div>
        <Link className="btn secondary" href="/projects">
          All projects
        </Link>
      </div>

      {error ? <div className="error">{error}</div> : null}

      <section className="panel stack">
        <form className="stack" onSubmit={onStartRun}>
          <div className="toolbar">
            <label>
              Max domains{" "}
              <input
                type="number"
                min={1}
                max={100}
                value={maxDomains}
                onChange={(e) => setMaxDomains(Number(e.target.value) || 20)}
                style={{ width: 100, marginLeft: 8 }}
              />
            </label>
            <label>
              Store age{" "}
              <select
                value={storeAgeFilter}
                onChange={(e) =>
                  setStoreAgeFilter(
                    e.target.value as "all" | "new" | "established" | "unknown",
                  )
                }
                style={{ marginLeft: 8 }}
              >
                <option value="all">All stores</option>
                <option value="new">New stores only</option>
                <option value="established">Established stores only</option>
                <option value="unknown">Unknown store age only</option>
              </select>
            </label>
            <button className="btn" type="submit" disabled={busy}>
              {busy ? "Queuing…" : "Queue discovery run"}
            </button>
          </div>
          <p style={{ color: "var(--muted)", margin: 0 }}>
            Store age uses public RDAP, certificate, and archive evidence when available. First
            discovered by this tool is not a creation date.
          </p>
          <label className="check-row">
            <input
              type="checkbox"
              checked={allCountries}
              onChange={(e) => setAllCountries(e.target.checked)}
            />
            All countries (any published country; unpublished still saved)
          </label>
          {!allCountries ? (
            <div className="stack">
              <p style={{ color: "var(--muted)", margin: 0 }}>
                Keep only shops that publish one of these countries on the storefront. Country is
                never guessed from hosting IPs or a myshopify.com name.
              </p>
              <div className="country-grid">
                {COUNTRY_CHOICES.map((country) => (
                  <label key={country.code} className="check-row">
                    <input
                      type="checkbox"
                      checked={selectedCountries.includes(country.code)}
                      onChange={() => toggleCountry(country.code)}
                    />
                    {country.name}
                  </label>
                ))}
              </div>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={keepUnknownCountry}
                  onChange={(e) => setKeepUnknownCountry(e.target.checked)}
                />
                Keep shops that do not publish a country
              </label>
            </div>
          ) : null}
        </form>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          Runs are queued and claimed by discovery workers. Status and the activity log update as
          the cycle runs.
        </p>
      </section>

      <div className="toolbar">
        <button
          type="button"
          className={tab === "runs" ? "btn" : "btn secondary"}
          onClick={() => setTab("runs")}
        >
          Runs
        </button>
        <button
          type="button"
          className={tab === "leads" ? "btn" : "btn secondary"}
          onClick={() => {
            setTab("leads");
            listLeads(projectId)
              .then(setLeads)
              .catch((err) =>
                setError(err instanceof ApiError ? err.message : "Could not load leads"),
              );
          }}
        >
          Leads ({leads.length})
        </button>
        <button
          type="button"
          className={tab === "products" ? "btn" : "btn secondary"}
          onClick={() => {
            setTab("products");
            listProducts(projectId)
              .then(setProducts)
              .catch((err) =>
                setError(err instanceof ApiError ? err.message : "Could not load products"),
              );
          }}
        >
          Products ({products.length})
        </button>
        <button
          type="button"
          className={tab === "rejected" ? "btn" : "btn secondary"}
          onClick={() => {
            setTab("rejected");
            listRejected(projectId)
              .then(setRejected)
              .catch((err) =>
                setError(err instanceof ApiError ? err.message : "Could not load rejected"),
              );
          }}
        >
          Rejected ({rejected.length})
        </button>
        <button
          type="button"
          className={tab === "research" ? "btn" : "btn secondary"}
          onClick={() => {
            setTab("research");
            Promise.all([listWatchlist(projectId), listSavedFilters(projectId)])
              .then(([watchRows, filterRows]) => {
                setWatchlist(watchRows);
                setSavedFilters(filterRows);
              })
              .catch((err) =>
                setError(err instanceof ApiError ? err.message : "Could not load research"),
              );
          }}
        >
          Research
        </button>
      </div>

      {tab === "runs" ? (
        <section className="panel stack">
          <div>
            <h2 style={{ marginTop: 0 }}>Discovery runs</h2>
            {runs.length === 0 ? (
              <div className="empty">No runs yet.</div>
            ) : (
              runs.map((run) => (
                <div className="list-row" key={run.id}>
                  <div>
                    <strong>{run.name || run.id}</strong>{" "}
                    <span className="badge">{run.status}</span>
                  </div>
                  <button type="button" className="btn secondary" onClick={() => openRun(run.id)}>
                    View
                  </button>
                </div>
              ))
            )}
          </div>
          {selectedRun ? (
            <div>
              <h3 style={{ marginBottom: 8 }}>
                {selectedRun.name || selectedRun.id}{" "}
                <span className="badge">{selectedRun.status}</span>
              </h3>
              {selectedRun.summary ? <p>{selectedRun.summary}</p> : null}
              {selectedRun.error ? <div className="error">{selectedRun.error}</div> : null}
              <div className="log-box">{selectedRun.log || "No activity log yet."}</div>
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "leads" ? (
        <section className="panel">
          <div className="toolbar">
            <input
              placeholder="Search leads"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <label>
              Theme{" "}
              <select value={themeFilter} onChange={(e) => setThemeFilter(e.target.value)}>
                <option value="all">All themes</option>
                <option value="undetected">Theme not identified</option>
                {themeOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Tracking{" "}
              <select value={trackingFilter} onChange={(e) => setTrackingFilter(e.target.value)}>
                <option value="all">All tracking</option>
                <option value="none">No tracking identified</option>
                {trackingOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn secondary"
              onClick={() =>
                downloadCsv(
                  `${project?.name || "project"}-leads.csv`,
                  filteredLeads.map((lead) => ({
                    ...lead,
                    tracking_technologies: (lead.tracking_technologies || [])
                      .map((tech) => `${tech.name} (${tech.confidence})`)
                      .join("; "),
                  })) as unknown as Record<string, unknown>[],
                  [
                    "domain",
                    "store_name",
                    "public_email",
                    "country",
                    "theme_name",
                    "theme_confidence",
                    "tracking_technologies",
                    "freshness_level",
                    "freshness_score",
                    "source",
                  ],
                )
              }
              disabled={filteredLeads.length === 0}
            >
              Export CSV
            </button>
          </div>
          <div className="table-wrap">
            {filteredLeads.length === 0 ? (
              <div className="empty">No leads match this view.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Domain</th>
                    <th>Store</th>
                    <th>Email</th>
                    <th>Country</th>
                    <th>Theme</th>
                    <th>Tracking</th>
                    <th>Freshness</th>
                    <th>Source</th>
                    <th>Watch</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredLeads.map((lead) => (
                    <tr key={lead.id}>
                      <td>{lead.domain}</td>
                      <td>{lead.store_name || "—"}</td>
                      <td>{lead.public_email || "—"}</td>
                      <td>{lead.country || "—"}</td>
                      <td>
                        {lead.theme_name ? (
                          <>
                            {lead.theme_name}
                            {lead.theme_confidence ? (
                              <span className="badge" style={{ marginLeft: 6 }}>
                                {lead.theme_confidence}
                              </span>
                            ) : null}
                          </>
                        ) : (
                          "Not identified"
                        )}
                      </td>
                      <td>
                        {(lead.tracking_technologies || []).length === 0
                          ? "None identified"
                          : (lead.tracking_technologies || []).map((tech) => (
                              <div key={`${lead.id}-${tech.name}`}>
                                {tech.name}{" "}
                                <span className="badge">{tech.confidence}</span>
                              </div>
                            ))}
                      </td>
                      <td>{lead.freshness_level || "—"}</td>
                      <td>{lead.source || "—"}</td>
                      <td>
                        <button
                          type="button"
                          className="btn secondary"
                          onClick={() => onToggleWatch(lead.domain)}
                        >
                          {watchedDomains.has(lead.domain) ? "Watching" : "Watch"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      ) : null}

      {tab === "products" ? (
        <section className="panel">
          <div className="toolbar">
            <input
              placeholder="Search products"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <label>
              Store{" "}
              <select
                value={productDomainFilter}
                onChange={(e) => setProductDomainFilter(e.target.value)}
              >
                <option value="all">All stores</option>
                {productDomainOptions.map((domain) => (
                  <option key={domain} value={domain}>
                    {domain}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Category{" "}
              <select
                value={productCategoryFilter}
                onChange={(e) => setProductCategoryFilter(e.target.value)}
              >
                <option value="all">All categories</option>
                <option value="uncategorized">No category published</option>
                {productCategoryOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn secondary"
              onClick={() =>
                downloadCsv(
                  `${project?.name || "project"}-products.csv`,
                  filteredProducts as unknown as Record<string, unknown>[],
                  [
                    "domain",
                    "title",
                    "price",
                    "currency",
                    "category",
                    "product_url",
                    "image_url",
                  ],
                )
              }
              disabled={filteredProducts.length === 0}
            >
              Export CSV
            </button>
          </div>
          <div className="table-wrap">
            {filteredProducts.length === 0 ? (
              <div className="empty">No products match this view.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Store</th>
                    <th>Product</th>
                    <th>Price</th>
                    <th>Category</th>
                    <th>Link</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredProducts.map((product) => (
                    <tr key={product.id}>
                      <td>{product.domain}</td>
                      <td>
                        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                          {product.image_url ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={product.image_url}
                              alt=""
                              width={40}
                              height={40}
                              style={{ objectFit: "cover", borderRadius: 4 }}
                            />
                          ) : null}
                          <span>{product.title || "Untitled"}</span>
                        </div>
                      </td>
                      <td>
                        {product.price
                          ? `${product.currency ? `${product.currency} ` : ""}${product.price}`
                          : "Not published"}
                      </td>
                      <td>{product.category || "Not published"}</td>
                      <td>
                        {product.product_url ? (
                          <a href={product.product_url} target="_blank" rel="noreferrer">
                            Open
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      ) : null}

      {tab === "research" ? (
        <section className="stack">
          <div className="panel stack">
            <div>
              <h2 style={{ marginTop: 0 }}>Compare stores</h2>
              <p style={{ color: "var(--muted)", margin: 0 }}>
                Side-by-side public fields only: country, freshness evidence, theme, tracking, and
                product catalogs. Sales, traffic, and revenue are never estimated.
              </p>
            </div>
            <form className="toolbar" onSubmit={onCompare}>
              <label>
                Store A{" "}
                <select value={compareA} onChange={(e) => setCompareA(e.target.value)}>
                  <option value="">Select store</option>
                  {leads.map((lead) => (
                    <option key={`a-${lead.id}`} value={lead.domain}>
                      {lead.domain}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Store B{" "}
                <select value={compareB} onChange={(e) => setCompareB(e.target.value)}>
                  <option value="">Select store</option>
                  {leads.map((lead) => (
                    <option key={`b-${lead.id}`} value={lead.domain}>
                      {lead.domain}
                    </option>
                  ))}
                </select>
              </label>
              <button className="btn" type="submit" disabled={busy}>
                Compare
              </button>
            </form>
            {compareResult ? (
              <div className="stack">
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Field</th>
                        <th>{compareResult.a.domain}</th>
                        <th>{compareResult.b.domain}</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>Store name</td>
                        <td>{compareResult.a.store_name || "—"}</td>
                        <td>{compareResult.b.store_name || "—"}</td>
                      </tr>
                      <tr>
                        <td>Country</td>
                        <td>{compareResult.a.country || "Not published"}</td>
                        <td>{compareResult.b.country || "Not published"}</td>
                      </tr>
                      <tr>
                        <td>Freshness / public age signal</td>
                        <td>
                          {compareResult.a.freshness_level || "—"}
                          {compareResult.a.freshness_score != null
                            ? ` (${compareResult.a.freshness_score})`
                            : ""}
                        </td>
                        <td>
                          {compareResult.b.freshness_level || "—"}
                          {compareResult.b.freshness_score != null
                            ? ` (${compareResult.b.freshness_score})`
                            : ""}
                        </td>
                      </tr>
                      <tr>
                        <td>Age evidence</td>
                        <td>{compareResult.a.freshness_evidence || "—"}</td>
                        <td>{compareResult.b.freshness_evidence || "—"}</td>
                      </tr>
                      <tr>
                        <td>Theme</td>
                        <td>
                          {compareResult.a.theme_name || "Not identified"}
                          {compareResult.a.theme_confidence
                            ? ` (${compareResult.a.theme_confidence})`
                            : ""}
                        </td>
                        <td>
                          {compareResult.b.theme_name || "Not identified"}
                          {compareResult.b.theme_confidence
                            ? ` (${compareResult.b.theme_confidence})`
                            : ""}
                        </td>
                      </tr>
                      <tr>
                        <td>Tracking</td>
                        <td>
                          {(compareResult.a.tracking_technologies || []).length
                            ? (compareResult.a.tracking_technologies || [])
                                .map((tech) => `${tech.name} (${tech.confidence})`)
                                .join(", ")
                            : "None identified"}
                        </td>
                        <td>
                          {(compareResult.b.tracking_technologies || []).length
                            ? (compareResult.b.tracking_technologies || [])
                                .map((tech) => `${tech.name} (${tech.confidence})`)
                                .join(", ")
                            : "None identified"}
                        </td>
                      </tr>
                      <tr>
                        <td>Products saved</td>
                        <td>{compareResult.a.product_count ?? 0}</td>
                        <td>{compareResult.b.product_count ?? 0}</td>
                      </tr>
                      <tr>
                        <td>First seen / last checked</td>
                        <td>
                          {(compareResult.a.first_seen || "—") +
                            " / " +
                            (compareResult.a.last_checked || "—")}
                        </td>
                        <td>
                          {(compareResult.b.first_seen || "—") +
                            " / " +
                            (compareResult.b.last_checked || "—")}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p style={{ color: "var(--muted)", margin: 0 }}>
                  Shared tracking:{" "}
                  {(compareResult.fields.tracking_shared || []).join(", ") || "none"}. Product
                  matches use public handle, then title — never shopify product ids across stores.
                </p>
                <div className="table-wrap">
                  <h3 style={{ marginTop: 0 }}>
                    Shared products ({compareResult.products.shared_count})
                  </h3>
                  {compareResult.products.shared.length === 0 ? (
                    <div className="empty">No reliably matched products.</div>
                  ) : (
                    <table>
                      <thead>
                        <tr>
                          <th>Match</th>
                          <th>{compareResult.a.domain}</th>
                          <th>{compareResult.b.domain}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {compareResult.products.shared.map((row) => (
                          <tr key={`${row.match_by}-${row.match_key}`}>
                            <td>
                              {row.match_by}: {row.match_key}
                            </td>
                            <td>
                              {row.a.title}
                              {row.a.price
                                ? ` — ${row.a.currency ? `${row.a.currency} ` : ""}${row.a.price}`
                                : " — price not published"}
                              {row.a.product_url ? (
                                <>
                                  {" "}
                                  <a href={row.a.product_url} target="_blank" rel="noreferrer">
                                    Open
                                  </a>
                                </>
                              ) : null}
                            </td>
                            <td>
                              {row.b.title}
                              {row.b.price
                                ? ` — ${row.b.currency ? `${row.b.currency} ` : ""}${row.b.price}`
                                : " — price not published"}
                              {row.b.product_url ? (
                                <>
                                  {" "}
                                  <a href={row.b.product_url} target="_blank" rel="noreferrer">
                                    Open
                                  </a>
                                </>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
                <div className="toolbar" style={{ alignItems: "flex-start" }}>
                  <div style={{ flex: 1 }}>
                    <h3 style={{ marginTop: 0 }}>
                      Only in {compareResult.a.domain} ({compareResult.products.only_a_count})
                    </h3>
                    <ul>
                      {compareResult.products.only_a.map((item) => (
                        <li key={`only-a-${item.title}-${item.handle}`}>
                          {item.title}
                          {item.product_url ? (
                            <>
                              {" "}
                              <a href={item.product_url} target="_blank" rel="noreferrer">
                                Open
                              </a>
                            </>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div style={{ flex: 1 }}>
                    <h3 style={{ marginTop: 0 }}>
                      Only in {compareResult.b.domain} ({compareResult.products.only_b_count})
                    </h3>
                    <ul>
                      {compareResult.products.only_b.map((item) => (
                        <li key={`only-b-${item.title}-${item.handle}`}>
                          {item.title}
                          {item.product_url ? (
                            <>
                              {" "}
                              <a href={item.product_url} target="_blank" rel="noreferrer">
                                Open
                              </a>
                            </>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          <div className="panel stack">
            <div>
              <h2 style={{ marginTop: 0 }}>Watchlist</h2>
              <p style={{ color: "var(--muted)", margin: 0 }}>
                Saved stores stay linked to their lead and product research for revisit tracking.
              </p>
            </div>
            {watchlist.length === 0 ? (
              <div className="empty">No watched stores yet. Use Watch on the Leads tab.</div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Domain</th>
                      <th>Store</th>
                      <th>Country</th>
                      <th>Theme</th>
                      <th>Products</th>
                      <th>Last checked</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {watchlist.map((item) => (
                      <tr key={item.id}>
                        <td>{item.domain}</td>
                        <td>{item.lead?.store_name || "—"}</td>
                        <td>{item.lead?.country || "Not published"}</td>
                        <td>{item.lead?.theme_name || "Not identified"}</td>
                        <td>{item.product_count ?? 0}</td>
                        <td>{item.lead?.last_checked || "—"}</td>
                        <td>
                          <button
                            type="button"
                            className="btn secondary"
                            onClick={() => onToggleWatch(item.domain)}
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="panel stack">
            <div>
              <h2 style={{ marginTop: 0 }}>Saved discovery filters</h2>
              <p style={{ color: "var(--muted)", margin: 0 }}>
                Reuse country, store-age, theme, and tracking settings for later runs.
              </p>
            </div>
            <form className="toolbar" onSubmit={onSaveFilter}>
              <input
                placeholder="Filter name"
                value={filterName}
                onChange={(e) => setFilterName(e.target.value)}
              />
              <button className="btn" type="submit" disabled={busy}>
                Save current settings
              </button>
            </form>
            {savedFilters.length === 0 ? (
              <div className="empty">No saved filter configurations yet.</div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Summary</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {savedFilters.map((item) => (
                      <tr key={item.id}>
                        <td>{item.name}</td>
                        <td>
                          age={String(item.config.store_age_filter || "all")}; countries=
                          {item.config.all_countries
                            ? "all"
                            : (item.config.target_countries || []).join(",") || "—"}
                          ; theme={String(item.config.theme_filter || "all")}; tracking=
                          {String(item.config.tracking_filter || "all")}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn secondary"
                            onClick={() => applySavedFilter(item)}
                          >
                            Apply
                          </button>{" "}
                          <button
                            type="button"
                            className="btn secondary"
                            onClick={() =>
                              deleteSavedFilter(projectId, item.id)
                                .then(() =>
                                  setSavedFilters((rows) =>
                                    rows.filter((row) => row.id !== item.id),
                                  ),
                                )
                                .catch((err) =>
                                  setError(
                                    err instanceof ApiError
                                      ? err.message
                                      : "Could not delete filter",
                                  ),
                                )
                            }
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === "rejected" ? (
        <section className="panel">
          <div className="toolbar">
            <input
              placeholder="Search rejected"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              type="button"
              className="btn secondary"
              onClick={() =>
                downloadCsv(
                  `${project?.name || "project"}-rejected.csv`,
                  filteredRejected as unknown as Record<string, unknown>[],
                  ["domain", "store_name", "public_email", "reason"],
                )
              }
              disabled={filteredRejected.length === 0}
            >
              Export CSV
            </button>
          </div>
          <div className="table-wrap">
            {filteredRejected.length === 0 ? (
              <div className="empty">No rejected candidates match this view.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Domain</th>
                    <th>Store</th>
                    <th>Email</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRejected.map((row) => (
                    <tr key={row.id}>
                      <td>{row.domain}</td>
                      <td>{row.store_name || "—"}</td>
                      <td>{row.public_email || "—"}</td>
                      <td>{row.reason || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}
