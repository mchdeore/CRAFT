import { OfficePreviewToolbar } from "@/components/citations/preview/office-preview-toolbar";
import type { DocumentRendererProps } from "@/components/citations/preview/renderer-contract";
import {
  cellKey,
  columnName,
  loadSpreadsheet,
  type SheetCell,
  type SheetModel,
} from "@/components/citations/preview/xlsx/spreadsheet-model";
import { FileSpreadsheetIcon } from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type WorkbookState =
  | { type: "loading" }
  | { type: "ready"; sheets: SheetModel[] }
  | { type: "error"; message: string };

export function XlsxRenderer({ blob, source }: DocumentRendererProps) {
  const initialSheet = source.sheetName;
  const citedRow = citedSpreadsheetRow(source.locator);
  const state = useWorkbook(blob);
  const [selectedSheet, setSelectedSheet] = useState(initialSheet);
  const [zoom, setZoom] = useState(1);
  const viewportRef = useRef<HTMLDivElement>(null);
  const sheetCanvasRef = useRef<HTMLDivElement>(null);

  const sheet = useMemo(() => {
    if (state.type !== "ready") return undefined;
    return (
      state.sheets.find((candidate) => candidate.name === selectedSheet) ??
      state.sheets[0]
    );
  }, [selectedSheet, state]);

  if (state.type === "loading") {
    return <p className="text-muted-foreground text-sm">Opening workbook…</p>;
  }
  if (state.type === "error") {
    return <p className="text-destructive text-sm">{state.message}</p>;
  }
  if (!sheet) {
    return <p className="text-muted-foreground text-sm">The workbook is empty.</p>;
  }

  const fitWidth = () => {
    const viewport = viewportRef.current;
    const table = sheetCanvasRef.current?.querySelector("table");
    if (!viewport || !table) return;
    setZoom(fitZoom((viewport.clientWidth - 16) / table.offsetWidth));
  };

  return (
    <div className="bg-background flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      <OfficePreviewToolbar
        application="Excel"
        color="#217346"
        icon={<FileSpreadsheetIcon className="size-4" />}
        zoom={zoom}
        onZoomChange={setZoom}
        onFitWidth={fitWidth}
      />

      <div className="bg-[#f3f3f3] flex min-h-9 shrink-0 items-end gap-px overflow-x-auto border-b px-2 pt-1 dark:bg-zinc-900">
        <div className="flex min-w-max items-end gap-px">
          {state.sheets.map((candidate) => (
            <button
              key={candidate.name}
              type="button"
              className={
                candidate.name === sheet.name
                  ? "bg-background border-primary border-b-2 px-3 py-1.5 text-xs font-medium"
                  : "text-muted-foreground hover:bg-background/60 px-3 py-1.5 text-xs"
              }
              onClick={() => setSelectedSheet(candidate.name)}
            >
              {candidate.name}
            </button>
          ))}
        </div>
      </div>

      <div ref={viewportRef} className="min-h-0 flex-1 overflow-auto">
        <div ref={sheetCanvasRef} className="min-w-max" style={{ zoom }}>
          <SheetGrid
            sheet={sheet}
            citedRow={sheet.name === initialSheet ? citedRow : undefined}
          />
        </div>
      </div>
    </div>
  );
}

function SheetGrid({ sheet, citedRow }: { sheet: SheetModel; citedRow?: number }) {
  const citedRowRef = useRef<HTMLTableRowElement>(null);

  useEffect(() => {
    citedRowRef.current?.scrollIntoView({ block: "center" });
  }, [citedRow, sheet.name]);

  return (
    <table className="border-separate border-spacing-0 text-[13px] text-black">
      <colgroup>
        <col style={{ width: 44 }} />
        {sheet.columnWidths.map((width, index) => (
          <col key={index} style={{ width, minWidth: width }} />
        ))}
      </colgroup>
      <thead className="sticky top-0 z-20">
        <tr>
          <th className="sticky left-0 z-30 h-6 border-r border-b bg-[#f3f3f3] dark:bg-zinc-800" />
          {Array.from({ length: sheet.columnCount }, (_, index) => (
            <th
              key={index}
              className="h-6 border-r border-b border-[#d0d0d0] bg-[#f3f3f3] px-1 text-center text-[11px] font-normal text-[#555] dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
            >
              {columnName(index + 1)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sheet.cells.map((row, rowIndex) => {
          const rowNumber = rowIndex + 1;
          const isCited = rowNumber === citedRow;
          return (
            <tr
              key={rowNumber}
              ref={isCited ? citedRowRef : undefined}
              style={{ height: sheet.rowHeights[rowIndex] }}
            >
              <th
                className={
                  isCited
                    ? "sticky left-0 z-10 border-r border-b border-amber-400 bg-amber-100 px-1 text-center text-[11px] font-semibold text-amber-900"
                    : "sticky left-0 z-10 border-r border-b border-[#d0d0d0] bg-[#f3f3f3] px-1 text-center text-[11px] font-normal text-[#555] dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                }
              >
                {rowNumber}
              </th>
              {row.map((cell, columnIndex) => {
                const merge = sheet.merges.get(cellKey(rowNumber, columnIndex + 1));
                if (merge && !merge.master) return null;
                return (
                  <td
                    key={columnIndex}
                    rowSpan={merge?.rowSpan}
                    colSpan={merge?.columnSpan}
                    className="border-r border-b border-[#d9d9d9] px-1.5 py-1 align-top dark:border-zinc-700"
                    style={{
                      ...cell.style,
                      ...(isCited
                        ? {
                            boxShadow:
                              "inset 0 2px rgb(245 158 11), inset 0 -2px rgb(245 158 11)",
                          }
                        : {}),
                    }}
                    title={cell.formula ? `=${cell.formula}` : undefined}
                  >
                    {cellContent(cell)}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function useWorkbook(blob: Blob): WorkbookState {
  const [state, setState] = useState<WorkbookState>({ type: "loading" });

  useEffect(() => {
    let isCurrent = true;

    void loadSpreadsheet(blob)
      .then((sheets) => {
        if (isCurrent) setState({ type: "ready", sheets });
      })
      .catch((error: unknown) => {
        if (isCurrent) {
          setState({
            type: "error",
            message:
              error instanceof Error ? error.message : "Workbook preview failed",
          });
        }
      });

    return () => {
      isCurrent = false;
    };
  }, [blob]);

  return state;
}

function cellContent(cell: SheetCell): ReactNode {
  if (!cell.hyperlink) return cell.text;
  return (
    <a
      href={cell.hyperlink}
      target="_blank"
      rel="noreferrer"
      className="text-blue-700 underline"
    >
      {cell.text}
    </a>
  );
}

function fitZoom(zoom: number): number {
  return Math.min(2, Math.max(0.3, Math.round(zoom * 10) / 10));
}

function citedSpreadsheetRow(locator: string | undefined): number | undefined {
  if (!locator) return undefined;
  const match = /^r-(\d+)$/.exec(locator);
  if (!match) return undefined;
  const row = Number.parseInt(match[1]!, 10);
  return Number.isInteger(row) && row > 0 ? row : undefined;
}
