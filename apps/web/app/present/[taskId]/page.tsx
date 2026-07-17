"use client";

import { useEffect, useMemo, useState } from "react";

import { activatePresentation, getDeck, getPresentationSession, navigatePresentation } from "../../../lib/api";
import type { ChartSpec, DeckSpec } from "../../../lib/types";

export default function Presentation({ params, searchParams }: { params: Promise<{ taskId: string }>; searchParams: Promise<{ revision?: string }> }) {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [requestedRevision, setRequestedRevision] = useState<number | undefined>(undefined);
  const [deck, setDeck] = useState<DeckSpec | null>(null);
  const [chart, setChart] = useState<ChartSpec | null>(null);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    Promise.all([params, searchParams]).then(([{ taskId }, query]) => {
      const revision = query.revision ? Number.parseInt(query.revision, 10) : undefined;
      setTaskId(taskId);
      setRequestedRevision(Number.isFinite(revision) ? revision : undefined);
      return Promise.all([getDeck(taskId, Number.isFinite(revision) ? revision : undefined), activatePresentation(taskId)]);
    }).then(([{ deck, chart }, session]) => {
      setDeck(deck);
      setChart(chart);
      setIndex(session.active_slide);
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
  const max = useMemo(() => Math.max(...(chart?.series[0]?.y ?? [1])), [chart]);

  return (
    <main className="presentation" onClick={() => taskId && navigatePresentation(taskId, "next").then((session) => setIndex(session.active_slide)).catch(() => undefined)}>
      <section className="slide" data-robin-presentation-ready={slide ? "true" : "false"} data-robin-task-id={taskId ?? ""} data-robin-revision={deck?.revision ?? ""}>
        {!slide && <h1>Loading Robin presentation</h1>}
        {slide?.type === "title" && (
          <div>
            <h1>{deck?.title}</h1>
            {slide.body.map((line) => <p key={line}>{line}</p>)}
          </div>
        )}
        {slide && slide.type !== "title" && slide.type !== "chart" && slide.type !== "key_metrics" && (
          <div>
            <h2>{slide.title}</h2>
            <ul>{slide.body.map((line) => <li key={line}>{line}</li>)}</ul>
          </div>
        )}
        {slide?.type === "key_metrics" && (
          <div>
            <h2>{slide.title}</h2>
            <div className="metric-grid">
              {Object.entries(slide.metrics).map(([label, value]) => (
                <div className="metric" key={label}>
                  <strong>{value}</strong>
                  <span>{label}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {slide?.type === "chart" && chart && (
          <div>
            <h2>{chart.title}</h2>
            <div className="bars">
              {chart.series[0].x.map((x, row) => (
                <div className="bar-row" key={x}>
                  <strong>{x}</strong>
                  <div><div className="bar-fill" style={{ width: `${(chart.series[0].y[row] / max) * 100}%` }} /></div>
                  <span>{chart.series[0].y[row].toFixed(1)}</span>
                </div>
              ))}
            </div>
            <p className="muted">{chart.subtitle}</p>
          </div>
        )}
      </section>
    </main>
  );
}
