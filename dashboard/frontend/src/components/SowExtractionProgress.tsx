"use client";

/**
 * Live extraction status — what the pipeline is actually doing, step by step.
 *
 * THERE IS NO STEP LIST IN THIS FILE, deliberately. It renders whatever the
 * backend reports, in the order it reported it. The tempting version — four
 * fixed phases ticked off as the artifact progresses — cannot be honest: it
 * shows the same steps in the same order regardless of what ran, claims
 * "identifying feature sections" on an ingest with zoning disabled, and stays
 * silent on gap repair, the variant cap and the cross-part merge, which are
 * exactly the stages worth knowing about. PRODUCT.md's first design principle
 * is that copy must never claim progress that isn't happening, so the rows
 * come from the code that did the work (app/services/sow_progress.py).
 *
 * A consequence worth expecting: the timeline is uneven. A short document
 * produces six rows, a twelve-part one produces eighty, and a skipped stage
 * says it was skipped. That unevenness IS the information.
 *
 * ── Presentation, and why it is not just a flat list ──────────────────────
 *
 * 1. GROUPING. The worker emits one "Reading part N of M" event and then
 *    every step of that part underneath it, all carrying the same
 *    part_number. Rendered flat, "Split the text into 13 sections" reads as a
 *    peer of "Reading part 1 of 15" rather than as something that happened
 *    INSIDE it. Consecutive events sharing a part_number are therefore one
 *    group: first event is the header, the rest nest under it. Grouping on
 *    CONSECUTIVE runs, not on part_number globally, is what keeps a re-entered
 *    part (should the worker ever emit one) from being folded backwards into
 *    an earlier, already-finished group.
 *
 * 2. RESOLVING `running`. See resolveStatus below. The short version: the
 *    backend never emits a matching `done` for "Reading {file}" or "Reading
 *    part N of M", so a literal render leaves those rows spinning forever —
 *    including long after the whole extraction has finished.
 */

import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Check, Minus } from "lucide-react";
import { apiGet } from "@/utils/apiClient";

interface IngestEvent {
  sequence: number;
  stage: string;
  status: "running" | "done" | "skipped" | "error";
  description: string;
  part_number?: number | null;
  detail?: Record<string, unknown> | null;
  created_at: string;
}

interface ProgressResponse {
  artifact_id: string;
  parse_status: "not_required" | "pending" | "processing" | "done" | "error";
  total_parts: number;
  events: IngestEvent[];
}

// Same set SowCheckpointsSection polls on: "pending" is included because a
// just-enqueued artifact sits briefly in pending before a worker picks it up,
// and stopping there would freeze the panel before the first real step.
const ACTIVE = new Set(["pending", "processing"]);

/** How a row is drawn, which is not always the status the backend sent. */
type RenderStatus = IngestEvent["status"] | "stalled";

/**
 * What a `running` event should actually be drawn as.
 *
 * The backend opens two stages with `running` and never closes them —
 * "Reading {file}" (sow_ingest.py, the read stage) and "Reading part N of M"
 * (the per-part stage). Neither has a corresponding DONE emit; the work they
 * describe is reported by the events that follow instead. Rendering the
 * status literally is why the top row span forever, including on a document
 * whose extraction had finished minutes earlier.
 *
 * The worker DOES close both of them, by emitting a second event on the same
 * stage (sow_ingest.py: `read`/DONE after the file is read, `part`/DONE at the
 * end of a part). It has to emit rather than edit the opening row, because
 * this panel polls `?after=<last sequence seen>` and would never re-read an
 * edited row. Those closing events are what `closedStages` below is built
 * from, and they are the primary signal — a real completion the server stated.
 *
 * The inference here is the fallback for the cases no emit can cover, and
 * `settled` is the caller's answer to "has anything happened since this stage
 * opened" — a later event for a child row, a later GROUP for a header row:
 *
 *   - settled -> that stage finished. SOW ingest is single-flight per
 *     artifact (sow_progress.emit's own comment, relied on for its unlocked
 *     sequence counter), so nothing can be emitted after a stage while that
 *     stage is still running.
 *   - not settled, run ended cleanly -> the artifact reached a terminal
 *     successful parse_status, which it cannot do with a stage outstanding.
 *     Done.
 *   - not settled, run ended badly -> `stalled`. This is the case that will
 *     always need inference: a worker killed mid-stage emits nothing at all,
 *     so no amount of backend diligence produces a closing event for it. A
 *     tick here would claim work that never happened; a spinner would promise
 *     a resolution that is never coming.
 *   - otherwise -> still running. Spin.
 */
function resolveStatus(
  status: IngestEvent["status"],
  settled: boolean,
  finished: boolean,
  failed: boolean,
): RenderStatus {
  if (status !== "running") return status;
  if (settled) return "done";
  if (!finished) return "running";
  return failed ? "stalled" : "done";
}

/** Identity of a stage within one run: the same stage on two parts is two stages. */
function stageKey(event: IngestEvent): string {
  return `${event.stage}:${event.part_number ?? ""}`;
}

interface EventGroup {
  key: number;
  header: IngestEvent;
  headerStatus: RenderStatus;
  children: { event: IngestEvent; status: RenderStatus }[];
  /**
   * Whether this group's rows should be drawn indented under its first row.
   *
   * True only when the backend actually OPENED a containing stage — a
   * `running` event such as "Reading part 3 of 15". On a single-part document
   * no `part` event is emitted at all (sow_ingest.py guards it on
   * total_parts > 1), so the group's first row is just its first step, and
   * indenting the rest beneath it would invent a hierarchy the run does not
   * have: "No UI naming reference for this project" is not the parent of
   * "Saved 3 runnable skills". Those groups render flat, as they did before
   * grouping existed.
   */
  nested: boolean;
}

/**
 * Group consecutive same-part events, and resolve each row's rendered status.
 *
 * Three things happen here that a flat map over `events` would not do.
 *
 * 1. FOLDING. The worker closes a stage with a second event on the same stage
 *    (see resolveStatus). Rendered literally that is two rows — "Reading part
 *    9 of 16" then "Finished part 9 of 16" — which roughly doubles the
 *    timeline and reads as repetition rather than progress. A closing DONE is
 *    therefore folded onto the row that opened the stage: the opening row
 *    keeps its wording and takes the tick, and the closing event is not drawn.
 *
 *    Only `done` and `skipped` are folded away. An `error` still resolves the
 *    opening row, but is ALSO drawn, because its description is the failure
 *    message — swallowing it would silently discard the one row that says
 *    what went wrong.
 *
 * 2. HEADERS SETTLE ON GROUPS, CHILDREN ON EVENTS. A child settles as soon as
 *    any later event exists — it is one step, and the next step starting
 *    proves it ended. A header does not: it stands for the whole part, and its
 *    own children are emitted while that part is still very much in flight.
 *    Settling a header on "a later event exists" would tick "Reading part 9 of
 *    16" the instant its first sub-step landed, leaving nothing on screen
 *    spinning through the minutes-long extraction call that follows — the
 *    panel would look finished while the work was still running. A header
 *    settles only when a LATER GROUP exists, i.e. the next part actually
 *    started.
 *
 * 3. A GROUP INHERITS ITS CHILDREN'S FAILURE. A part whose extraction errored
 *    returns early and emits no closing event, so nothing else would stop its
 *    header spinning (or, once the next part began, would tick it — claiming a
 *    part succeeded when it did not). A group with a failed step failed.
 */
function buildGroups(
  events: IngestEvent[],
  finished: boolean,
  failed: boolean,
): EventGroup[] {
  const lastSequence = events.length ? events[events.length - 1].sequence : -1;

  // Which stages the worker explicitly closed, and how. Built in a first pass
  // because a stage's closing event always arrives after the row it resolves.
  const closedStages = new Map<string, IngestEvent["status"]>();
  const awaitingClose = new Set<string>();
  for (const event of events) {
    const key = stageKey(event);
    if (event.status === "running") {
      awaitingClose.add(key);
    } else if (awaitingClose.has(key)) {
      closedStages.set(key, event.status);
      awaitingClose.delete(key);
    }
  }

  // A closing event is drawn only when it carries a message worth reading.
  // Keyed off closedStages, which by construction only holds stages that were
  // opened by a running event, so this stays O(1) per event.
  const isFoldedAway = (event: IngestEvent) =>
    (event.status === "done" || event.status === "skipped") &&
    closedStages.get(stageKey(event)) === event.status;

  const statusOf = (event: IngestEvent, settled: boolean): RenderStatus => {
    const closedAs = event.status === "running" ? closedStages.get(stageKey(event)) : undefined;
    if (closedAs) return closedAs;
    return resolveStatus(event.status, settled, finished, failed);
  };

  const groups: EventGroup[] = [];
  for (const event of events) {
    const open = groups[groups.length - 1];
    // A new group starts on any change of part_number, including back to
    // null. `?? null` normalises undefined (absent in the payload) so it
    // compares equal to an explicit null rather than opening a phantom group.
    const samePart =
      open !== undefined && (open.header.part_number ?? null) === (event.part_number ?? null);
    if (!samePart) {
      // headerStatus is a placeholder until the loop is done and "is there a
      // later group" is actually knowable.
      groups.push({
        key: event.sequence,
        header: event,
        headerStatus: event.status,
        children: [],
        nested: event.status === "running",
      });
      continue;
    }
    // Folded onto the header/child that opened the stage rather than drawn.
    // The status it carries is already in closedStages.
    if (isFoldedAway(event)) continue;
    open.children.push({
      event,
      status: statusOf(event, event.sequence !== lastSequence),
    });
  }

  return groups.map((group, i) => {
    const headerStatus = statusOf(group.header, i < groups.length - 1);
    const childFailed = group.children.some((c) => c.status === "error");
    return {
      ...group,
      headerStatus: childFailed && headerStatus !== "error" ? "error" : headerStatus,
    };
  });
}

/**
 * The running marker: a metaball orb.
 *
 * Authored at 100x100 because that is the size the effect was drawn for —
 * the blur radii that fuse the seven polygons into one silhouette are
 * absolute, so redrawing the geometry smaller dissolves it. The whole thing
 * is scaled into an 18px slot instead (--sow-orb-scale, unitless — see the
 * warning on it in global.css). Styles live there too (`.sow-orb`); only the
 * mask id is per-instance, since a document with two concurrent running rows
 * would otherwise share one animated mask.
 */
function RunningOrb() {
  // useId yields ":r0:" — legal in an id attribute, but the colons make it a
  // hostile thing to put inside url(#…), so strip them.
  const maskId = `sow-orb-mask-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const maskUrl = `url(#${maskId})`;

  return (
    <span className="sow-orb" style={{ "--sow-orb-scale": 0.18 } as CSSProperties}>
      <svg width={100} height={100} viewBox="0 0 100 100" aria-hidden="true" focusable="false">
        <defs>
          <mask id={maskId}>
            <polygon points="0,0 100,0 100,100 0,100" fill="black" />
            <polygon points="25,25 75,25 50,75" fill="white" />
            <polygon points="50,25 75,75 25,75" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
          </mask>
        </defs>
      </svg>
      <span
        className="sow-orb__box"
        style={{ mask: maskUrl, WebkitMask: maskUrl }}
      />
    </span>
  );
}

function StatusIcon({ status }: { status: RenderStatus }) {
  if (status === "running") return <RunningOrb />;
  if (status === "error") {
    return <AlertCircle className="h-4 w-4 text-red-500" />;
  }
  if (status === "skipped") {
    // A dash, not a tick. A skipped stage did no work, and a green check
    // against "the repair pass never ran" would read as completed work.
    return <Minus className="h-4 w-4 text-gray-300" />;
  }
  if (status === "stalled") {
    // Same dash, different reason: this step never reported an outcome
    // because the run died under it. See resolveStatus.
    return <Minus className="h-4 w-4 text-amber-500" />;
  }
  return <Check className="h-4 w-4 text-green-600" />;
}

function textToneFor(status: RenderStatus): string {
  if (status === "skipped") return "text-gray-400";
  if (status === "error") return "text-red-700";
  if (status === "stalled") return "text-amber-700";
  return "text-gray-700";
}

export default function SowExtractionProgress({
  artifactId,
}: {
  artifactId: string | null;
}) {
  // Events accumulate across polls; each request asks only for what is new.
  // Keeping them here rather than refetching the whole timeline every two
  // seconds matters on a twelve-part document, where it grows past a hundred
  // rows and the panel stays open for minutes.
  const [events, setEvents] = useState<IngestEvent[]>([]);
  const lastSeq = useRef(0);
  const [active, setActive] = useState(true);
  // The artifact's own verdict, kept because the events alone cannot give it.
  // A worker killed mid-stage writes no error event — there is no code left
  // running to write one — so a run can fail with a timeline that contains
  // nothing but successes. Without this, that run's dead stage resolves as
  // "the run ended cleanly, so this finished" and quietly ticks.
  const [parseStatus, setParseStatus] = useState<ProgressResponse["parse_status"] | null>(null);

  // The list is a fixed-height scroller (see the render), so new steps land
  // below the fold. These two keep it following the run without hijacking a
  // reader who has scrolled up to re-read an earlier step.
  const scrollerRef = useRef<HTMLUListElement | null>(null);
  const stickToBottom = useRef(true);

  // A different artifact is a different run. Without this reset, re-running
  // extraction would append the new run's steps to the old run's timeline.
  useEffect(() => {
    setEvents([]);
    lastSeq.current = 0;
    stickToBottom.current = true;
    setParseStatus(null);
    setActive(true);
  }, [artifactId]);

  const { isError } = useQuery<ProgressResponse>({
    queryKey: ["sow-progress", artifactId, "poll"],
    enabled: !!artifactId && active,
    refetchInterval: active ? 2000 : false,
    retry: false,
    queryFn: async () => {
      const data: ProgressResponse = await apiGet(
        `/api/v1/visual-audits/sow/${artifactId}/progress?after=${lastSeq.current}`
      );
      if (data.events.length) {
        lastSeq.current = data.events[data.events.length - 1].sequence;
        setEvents((prev) => [...prev, ...data.events]);
      }
      setParseStatus(data.parse_status);
      // Stop on a terminal artifact status rather than on "no new events":
      // a slow stage can legitimately produce nothing for a while, and
      // stopping there would freeze the panel mid-run.
      if (!ACTIVE.has(data.parse_status)) setActive(false);
      return data;
    },
  });

  // Guarded on stickToBottom so it only fires for a reader who is already at
  // the end — someone who scrolled up to re-read an earlier step keeps their
  // position instead of being yanked back down every two seconds.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !stickToBottom.current) return;
    el.scrollTop = el.scrollHeight;
  }, [events]);

  if (!artifactId) return null;
  // 404 / feature off / a poll that cannot reach the server: show nothing
  // rather than an error box. The extraction itself reports its own failures
  // through the button beside this panel.
  if (isError && events.length === 0) return null;
  if (events.length === 0 && !active) return null;

  // Either signal is enough: a stage that reported its own failure, or an
  // artifact the backend marked errored without any stage getting the chance
  // to say so.
  const failed = parseStatus === "error" || events.some((e) => e.status === "error");
  const finished = !active;
  const groups = buildGroups(events, finished, failed);

  return (
    <div
      className={`mt-4 rounded-lg border px-4 py-3 ${
        failed
          ? "border-red-200 bg-red-50/50"
          : finished
          ? "border-green-200 bg-green-50/50"
          : "border-gray-200 bg-gray-50/60"
      }`}
      aria-live="polite"
      aria-busy={active}
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-medium text-gray-700">Live extraction status</p>
        <span
          className={`text-xs font-medium ${
            failed
              ? "text-red-600"
              : finished
              ? "text-green-700"
              : "text-blue-600"
          }`}
        >
          {failed
            ? "Failed"
            : finished
            ? "Extraction complete — new Skills/TDDs added to checkpoints."
            : "Processing"}
        </span>
      </div>

      {events.length === 0 ? (
        <p className="text-xs text-gray-500">Queued — waiting for a worker to pick it up…</p>
      ) : (
        // Fixed height, internal scroll. A twelve-part document produces
        // eighty-odd rows; unbounded, this panel pushed the export bar, the
        // rewrite panel and the whole document several screens down while it
        // ran, and the page grew under the reader as each step arrived.
        // max-h in px rather than vh so the box is a predictable size
        // regardless of viewport — same reasoning as the ledger table on the
        // SOW detail page.
        <ul
          ref={scrollerRef}
          className="max-h-64 space-y-1 overflow-y-auto"
          onScroll={(e) => {
            const el = e.currentTarget;
            // 24px of slack: "close enough to the bottom" survives a
            // fractional scrollHeight and a row arriving mid-scroll.
            stickToBottom.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          {groups.map((group) => (
            <li key={group.key}>
              {/* items-center, not items-start: the marker belongs on the
                  text's centreline. These descriptions are one line in
                  practice — the longest the backend emits is the naming
                  reference line — so there is no first-line-anchoring case
                  to protect here. */}
              <div className="flex items-center gap-2.5 rounded-md bg-white px-3 py-2">
                <span className="relative flex h-5 w-5 flex-none items-center justify-center">
                  <StatusIcon status={group.headerStatus} />
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className={`text-xs ${group.nested ? "font-medium" : ""} ${textToneFor(
                      group.headerStatus,
                    )}`}
                  >
                    {group.header.description}
                  </span>
                  {group.nested && group.header.part_number != null && (
                    <span className="ml-1.5 text-[11px] font-normal text-gray-400">
                      part {group.header.part_number}
                    </span>
                  )}
                </span>
              </div>

              {group.children.length > 0 && (
                // Indented under the header only when the backend really
                // opened a containing stage (see EventGroup.nested). The
                // indent plus the hairline connector is what makes those read
                // as steps INSIDE the row above rather than peers of it;
                // `relative` on each row anchors its own connector stub.
                <ul className={`mt-1 space-y-1 ${group.nested ? "pl-[26px]" : ""}`}>
                  {group.children.map(({ event, status }) => (
                    <li
                      key={event.sequence}
                      className={
                        group.nested
                          ? `relative flex items-center gap-2.5 rounded-md bg-white px-3 py-1.5
                             before:absolute before:left-[-15px] before:top-1/2 before:h-px
                             before:w-[11px] before:bg-gray-200 before:content-['']`
                          : "flex items-center gap-2.5 rounded-md bg-white px-3 py-2"
                      }
                    >
                      <span className="relative flex h-5 w-5 flex-none items-center justify-center">
                        <StatusIcon status={status} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={`text-xs ${textToneFor(status)}`}>
                          {event.description}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
