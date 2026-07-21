"use client";

import { useEffect, useMemo, useState } from "react";

import { getDeck, getPresentationSession, navigatePresentation } from "../../../lib/api";
import type { ChartSpec, DeckSpec } from "../../../lib/types";

type SlideSpec = DeckSpec["slides"][number];

const slideLabels: Record<string, string> = {
  executive_summary: "Executive brief",
  findings: "Evidence",
  key_metrics: "Signal check",
  chart: "Performance",
  methodology: "How we got here",
  sources: "Source index",
};

function splitColumns<T>(items: T[]): [T[], T[]] {
  const midpoint = Math.ceil(items.length / 2);
  return [items.slice(0, midpoint), items.slice(midpoint)];
}

function displayValue(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
}

function SlideHeader({ slide, index, count }: { slide: SlideSpec; index: number; count: number }) {
  return (
    <header className="deck-header">
      <div>
        <span className="deck-kicker">{slideLabels[slide.type] ?? "Robin briefing"}</span>
        <h2>{slide.title}</h2>
      </div>
      <span className="deck-page">{String(index + 1).padStart(2, "0")} / {String(count).padStart(2, "0")}</span>
    </header>
  );
}

function EvidenceSlide({ slide }: { slide: SlideSpec }) {
  const [left, right] = splitColumns(slide.body.slice(0, 5));
  return (
    <div className={`evidence-layout ${right.length === 0 ? "single" : ""}`}>
      {[left, right].filter((column) => column.length).map((column, columnIndex) => (
        <div className="evidence-column" key={columnIndex}>
          {column.map((line, rowIndex) => {
            const itemIndex = columnIndex === 0 ? rowIndex : left.length + rowIndex;
            return (
              <article className={`evidence-item ${itemIndex === 0 ? "lead" : ""}`} key={`${itemIndex}-${line}`}>
                <span>{String(itemIndex + 1).padStart(2, "0")}</span>
                <p>{line}</p>
              </article>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function MetricsSlide({ slide }: { slide: SlideSpec }) {
  const metrics = Object.entries(slide.metrics).slice(0, 4);
  return (
    <div className="metrics-layout">
      <div className={`metric-grid metric-count-${metrics.length}`}>
        {metrics.map(([label, value], index) => (
          <article className="metric-card" key={label}>
            <span className="metric-index">0{index + 1}</span>
            <strong>{value}</strong>
            <span className="metric-label">{label}</span>
          </article>
        ))}
      </div>
      {slide.body[0] && <p className="metric-takeaway">{slide.body[0]}</p>}
    </div>
  );
}

function ChartSlide({ chart, slide }: { chart: ChartSpec; slide: SlideSpec }) {
  const allValues = chart.series.flatMap((series) => series.y);
  const max = Math.max(...allValues, 1);
  const categories = chart.series[0]?.x ?? [];
  const insight = slide.body[0] || chart.subtitle;
  return (
    <div className="chart-layout">
      <div className="chart-panel">
        <div className="chart-legend">
          {chart.series.map((series, index) => (
            <span key={series.name}><i className={`series-swatch series-${index}`} />{series.name}</span>
          ))}
        </div>
        <div className="grouped-chart" role="img" aria-label={`${chart.title}. ${chart.subtitle ?? ""}`}>
          {categories.map((category, categoryIndex) => (
            <div className="chart-group" key={category}>
              <div className="chart-bars">
                {chart.series.map((series, seriesIndex) => {
                  const value = series.y[categoryIndex] ?? 0;
                  return (
                    <div className="bar-wrap" key={series.name}>
                      <span>{displayValue(value)}</span>
                      <div className={`chart-bar series-${seriesIndex}`} style={{ height: `${Math.max((value / max) * 100, 3)}%` }} />
                    </div>
                  );
                })}
              </div>
              <strong>{category}</strong>
            </div>
          ))}
        </div>
        <p className="chart-source">{chart.source_note}</p>
      </div>
      <aside className="chart-insight">
        <span>What matters</span>
        <p>{insight}</p>
      </aside>
    </div>
  );
}

function MethodologySlide({ slide }: { slide: SlideSpec }) {
  return (
    <div className="method-layout">
      {slide.body.slice(0, 5).map((step, index) => (
        <article className="method-step" key={step}>
          <div><span>{index + 1}</span></div>
          <p>{step}</p>
        </article>
      ))}
    </div>
  );
}

function SourcesSlide({ deck, slide }: { deck: DeckSpec; slide: SlideSpec }) {
  const sources = deck.sources.length
    ? deck.sources
    : slide.body.map((line) => ({ label: line, path: "", note: "" }));
  return (
    <div className="sources-layout">
      {sources.slice(0, 8).map((source, index) => (
        <article className="source-item" key={`${source.path}-${source.label}`}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div>
            <strong>{source.label}</strong>
            <p>{source.note || source.path}</p>
          </div>
        </article>
      ))}
    </div>
  );
}

function TitleSlide({ deck, slide }: { deck: DeckSpec; slide: SlideSpec }) {
  return (
    <div className="title-layout">
      <div className="title-copy">
        <span className="title-eyebrow">Robin briefing</span>
        <h1>{deck.title}</h1>
        {slide.body[0] && <p>{slide.body[0]}</p>}
      </div>
      <div className="title-mark" aria-hidden="true"><span /></div>
      <footer><span>Grounded analysis</span><span>Revision {deck.revision}</span></footer>
    </div>
  );
}

export default function Presentation({ params, searchParams }: { params: Promise<{ taskId: string }>; searchParams: Promise<{ revision?: string }> }) {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [requestedRevision, setRequestedRevision] = useState<number | undefined>(undefined);
  const [deck, setDeck] = useState<DeckSpec | null>(null);
  const [chart, setChart] = useState<ChartSpec | null>(null);
  const [index, setIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([params, searchParams]).then(([{ taskId }, query]) => {
      const revision = query.revision ? Number.parseInt(query.revision, 10) : undefined;
      setTaskId(taskId);
      setRequestedRevision(Number.isFinite(revision) ? revision : undefined);
      return Promise.all([getDeck(taskId, Number.isFinite(revision) ? revision : undefined), getPresentationSession(taskId)]);
    }).then(([{ deck, chart }, session]) => {
      setDeck(deck);
      setChart(chart);
      setIndex(session.active_slide);
      setError(null);
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Unable to load Robin presentation");
    });
  }, [params, searchParams]);

  useEffect(() => {
    if (!taskId) return undefined;
    const id = window.setInterval(() => {
      getPresentationSession(taskId).then((session) => {
        setIndex(session.active_slide);
        if (requestedRevision === undefined) {
          getDeck(taskId).then(({ deck, chart }) => {
            setDeck((current) => (current?.revision === deck.revision ? current : deck));
            setChart(chart);
          }).catch(() => undefined);
        }
      }).catch(() => undefined);
    }, 700);
    return () => window.clearInterval(id);
  }, [taskId, requestedRevision]);

  useEffect(() => {
    async function go(action: "next" | "previous") {
      if (!taskId) return;
      const session = await navigatePresentation(taskId, action);
      setIndex(session.active_slide);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "ArrowRight" || event.key === " ") void go("next");
      if (event.key === "ArrowLeft") void go("previous");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [taskId]);

  const slide = deck?.slides[index];
  const slideCount = deck?.slides.length ?? 0;
  const progress = useMemo(() => slideCount ? ((index + 1) / slideCount) * 100 : 0, [index, slideCount]);

  return (
    <main className="presentation" onClick={() => taskId && navigatePresentation(taskId, "next").then((session) => setIndex(session.active_slide)).catch(() => undefined)}>
      <section className={`slide slide-${slide?.type ?? "loading"}`} data-robin-presentation-ready={slide && !error ? "true" : "false"} data-robin-task-id={taskId ?? ""} data-robin-revision={deck?.revision ?? ""}>
        {error && <div className="deck-error" role="alert" data-robin-presentation-error="true">{error}</div>}
        {!slide && !error && <div className="deck-loading"><span />Loading Robin presentation</div>}
        {slide && deck && slide.type === "title" && <TitleSlide deck={deck} slide={slide} />}
        {slide && deck && slide.type !== "title" && (
          <div className="content-slide">
            <SlideHeader slide={slide} index={index} count={slideCount} />
            <div className="deck-content">
              {slide.type === "key_metrics" && <MetricsSlide slide={slide} />}
              {slide.type === "chart" && chart && <ChartSlide chart={chart} slide={slide} />}
              {slide.type === "chart" && !chart && <EvidenceSlide slide={slide} />}
              {slide.type === "methodology" && <MethodologySlide slide={slide} />}
              {slide.type === "sources" && <SourcesSlide deck={deck} slide={slide} />}
              {!['key_metrics', 'chart', 'methodology', 'sources'].includes(slide.type) && <EvidenceSlide slide={slide} />}
            </div>
            <footer className="deck-footer"><span>ROBIN · REV {deck.revision}</span><i><b style={{ width: `${progress}%` }} /></i></footer>
          </div>
        )}
      </section>
    </main>
  );
}
