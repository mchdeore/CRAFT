import type {
  Alignment,
  Border,
  Cell,
  Fill,
  Worksheet,
} from "exceljs";
import type { CSSProperties } from "react";
import {
  checkParsedSpreadsheet,
  checkSpreadsheetSize,
  preflightSpreadsheet,
  spreadsheetRange,
  SpreadsheetLimitError,
} from "./spreadsheet-bounds";

export interface SheetModel {
  name: string;
  columnCount: number;
  columnWidths: number[];
  rowHeights: number[];
  merges: Map<string, MergeCell>;
  cells: SheetCell[][];
}

export interface SheetCell {
  text: string;
  hyperlink?: string;
  formula?: string;
  style: CSSProperties;
}

interface MergeCell {
  master: boolean;
  rowSpan: number;
  columnSpan: number;
}

export async function loadSpreadsheet(blob: Blob): Promise<SheetModel[]> {
  try {
    checkSpreadsheetSize(blob.size);
    const bytes = await preflightSpreadsheet(new Uint8Array(await blob.arrayBuffer()));
    // Keep ExcelJS out of the initial application bundle, and reject excessive
    // archive/range input before its eager workbook parser can expand it.
    const { default: ExcelJS } = await import("exceljs");
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.load(bytes.buffer);
    checkParsedSpreadsheet(workbook.worksheets);
    return workbook.worksheets.map(buildSheetModel);
  } catch (error) {
    throw new Error(error instanceof SpreadsheetLimitError ? error.message : "Workbook preview failed", { cause: error });
  }
}

export function cellKey(row: number, column: number): string {
  return `${row}:${column}`;
}

export function columnName(column: number): string {
  let result = "";
  for (let value = column; value > 0; value = Math.floor((value - 1) / 26)) {
    result = String.fromCharCode(((value - 1) % 26) + 65) + result;
  }
  return result;
}

function buildSheetModel(worksheet: Worksheet): SheetModel {
  const rowCount = Math.max(worksheet.rowCount, 1);
  const columnCount = Math.max(worksheet.columnCount, 1);
  const cells = Array.from({ length: rowCount }, (_, rowIndex) =>
    Array.from({ length: columnCount }, (_, columnIndex) => {
      const cell = worksheet.getCell(rowIndex + 1, columnIndex + 1);
      return {
        text: cell.text,
        ...(cell.hyperlink ? { hyperlink: cell.hyperlink } : {}),
        ...(cell.formula ? { formula: cell.formula } : {}),
        style: cellStyle(cell),
      };
    }),
  );

  return {
    name: worksheet.name,
    columnCount,
    cells,
    merges: mergeCells(worksheet.model.merges),
    columnWidths: Array.from({ length: columnCount }, (_, index) => {
      const width = worksheet.getColumn(index + 1).width ?? 10;
      return Math.min(420, Math.max(48, Math.round(width * 7 + 8)));
    }),
    rowHeights: Array.from({ length: rowCount }, (_, index) => {
      const height = worksheet.getRow(index + 1).height ?? 15;
      return Number.isFinite(height)
        ? Math.min(546, Math.max(20, Math.round(height * (4 / 3))))
        : 20;
    }),
  };
}

function mergeCells(ranges: string[]): Map<string, MergeCell> {
  const result = new Map<string, MergeCell>();
  for (const range of ranges) {
    const { start, end } = spreadsheetRange(range);
    for (let row = start.row; row <= end.row; row += 1) {
      for (let column = start.column; column <= end.column; column += 1) {
        result.set(cellKey(row, column), {
          master: row === start.row && column === start.column,
          rowSpan: end.row - start.row + 1,
          columnSpan: end.column - start.column + 1,
        });
      }
    }
  }
  return result;
}

function cellStyle(cell: Cell): CSSProperties {
  const font = cell.font;
  const alignment = cell.alignment;
  return {
    background: fillColor(cell.fill),
    color: argbColor(font?.color?.argb),
    fontFamily: font?.name,
    fontSize: font?.size ? `${font.size}pt` : undefined,
    fontWeight: font?.bold ? 700 : undefined,
    fontStyle: font?.italic ? "italic" : undefined,
    textDecoration: font?.underline ? "underline" : undefined,
    textAlign: horizontalAlignment(alignment),
    verticalAlign: verticalAlignment(alignment),
    whiteSpace: alignment?.wrapText ? "normal" : "nowrap",
    borderTop: borderStyle(cell.border?.top),
    borderRight: borderStyle(cell.border?.right),
    borderBottom: borderStyle(cell.border?.bottom),
    borderLeft: borderStyle(cell.border?.left),
  };
}

function fillColor(fill: Fill | undefined): string | undefined {
  return fill?.type === "pattern" && fill.pattern === "solid"
    ? argbColor(fill.fgColor?.argb)
    : undefined;
}

function argbColor(argb: string | undefined): string | undefined {
  if (!argb || !/^[a-f\d]{8}$/i.test(argb)) return undefined;
  const alpha = Number.parseInt(argb.slice(0, 2), 16) / 255;
  const red = Number.parseInt(argb.slice(2, 4), 16);
  const green = Number.parseInt(argb.slice(4, 6), 16);
  const blue = Number.parseInt(argb.slice(6, 8), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function borderStyle(border: Partial<Border> | undefined): string | undefined {
  if (!border?.style) return undefined;
  const width =
    border.style === "thick"
      ? 3
      : border.style.startsWith("medium")
        ? 2
        : 1;
  const style = border.style.includes("dash")
    ? "dashed"
    : border.style === "dotted"
      ? "dotted"
      : "solid";
  return `${width}px ${style} ${argbColor(border.color?.argb) ?? "#888"}`;
}

function horizontalAlignment(
  alignment: Partial<Alignment> | undefined,
): CSSProperties["textAlign"] {
  if (alignment?.horizontal === "center") return "center";
  if (alignment?.horizontal === "right") return "right";
  if (alignment?.horizontal === "justify") return "justify";
  return alignment?.horizontal === "left" ? "left" : undefined;
}

function verticalAlignment(
  alignment: Partial<Alignment> | undefined,
): CSSProperties["verticalAlign"] {
  if (alignment?.vertical === "middle") return "middle";
  if (alignment?.vertical === "bottom") return "bottom";
  return alignment?.vertical === "top" ? "top" : undefined;
}
