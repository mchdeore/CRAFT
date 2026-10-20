import { Unzip, UnzipInflate, zipSync } from "fflate";
import { SaxesParser } from "saxes";
import type { Cell, Worksheet } from "exceljs";

// These are preview budgets, independent of Excel's file-format maxima and
// the transport's compressed-byte bound. Check both before and after parsing.
const limits = {
  rows: 2_000,
  columns: 100,
  cellsPerSheet: 20_000,
  cellsPerWorkbook: 50_000,
  displayTextPerCell: 32 * 1024,
  displayTextPerWorkbook: 8 * 1024 * 1024,
  sheets: 20,
  sheetId: 256,
  mergedCellsPerRange: 2_000,
  mergedCellsPerWorkbook: 10_000,
  mergeRanges: 256,
  parserExpandedCells: 50_000,
  archiveBytes: 10 * 1024 * 1024,
  archiveEntries: 256,
  entryBytes: 8 * 1024 * 1024,
  inflatedBytes: 32 * 1024 * 1024,
  xmlBytes: 24 * 1024 * 1024,
  xmlNodes: 250_000,
  xmlDepth: 64,
} as const;

export class SpreadsheetLimitError extends Error {
  constructor() {
    super("This workbook exceeds preview limits.");
  }
}

interface Extent {
  rows: number;
  columns: number;
}

interface CellAddress {
  row: number;
  column: number;
}

function requireLimit(condition: boolean): void {
  if (!condition) throw new SpreadsheetLimitError();
}

function positiveInteger(value: string | undefined): number {
  if (!value || !/^\d+$/.test(value)) throw new Error("Invalid workbook range");
  const number = Number(value);
  requireLimit(Number.isSafeInteger(number) && number > 0);
  return number;
}

function cellAddress(value: string): CellAddress {
  const match = /^\$?([A-Z]+)\$?(\d+)$/.exec(value);
  if (!match) throw new Error("Invalid workbook range");
  let column = 0;
  for (const character of match[1]!) {
    column = column * 26 + character.charCodeAt(0) - 64;
    requireLimit(column <= limits.columns);
  }
  const row = positiveInteger(match[2]);
  requireLimit(row <= limits.rows);
  return { row, column };
}

export function spreadsheetRange(value: string): {
  start: CellAddress;
  end: CellAddress;
  cells: number;
} {
  const parts = value.split(":");
  if (parts.length > 2) throw new Error("Invalid workbook range");
  const first = cellAddress(parts[0]!);
  const last = cellAddress(parts[1] ?? parts[0]!);
  const start = { row: Math.min(first.row, last.row), column: Math.min(first.column, last.column) };
  const end = { row: Math.max(first.row, last.row), column: Math.max(first.column, last.column) };
  return { start, end, cells: (end.row - start.row + 1) * (end.column - start.column + 1) };
}

function checkExtent({ rows, columns }: Extent): number {
  requireLimit(Number.isSafeInteger(rows) && rows >= 1 && rows <= limits.rows);
  requireLimit(Number.isSafeInteger(columns) && columns >= 1 && columns <= limits.columns);
  const cells = rows * columns;
  requireLimit(cells <= limits.cellsPerSheet);
  return cells;
}

export function checkSpreadsheetSize(size: number): void {
  requireLimit(size <= limits.archiveBytes);
}

// Match ExcelJS's eager XML-part loaders, which also accept trailing filename
// suffixes. VML drawings contain XML despite their .vml extension.
const parsedXmlPartNames = [
  /xl\/worksheets\/_rels\/sheet\d+\.xml\.rels/,
  /xl\/theme\/[a-zA-Z0-9]+\.xml/,
  /xl\/drawings\/[a-zA-Z0-9]+\.xml/,
  /xl\/comments\d+\.xml/,
  /xl\/tables\/table\d+\.xml/,
  /xl\/drawings\/_rels\/[a-zA-Z0-9]+\.xml\.rels/,
  /xl\/drawings\/vmlDrawing\d+\.vml/,
];

// ExcelJS uses the central directory, while streaming unzip reads local
// headers. Repack only the checked entries so conflicting ZIP names/offsets
// cannot make ExcelJS parse content that bypassed the worksheet preflight.
export async function preflightSpreadsheet(bytes: Uint8Array): Promise<Uint8Array<ArrayBuffer>> {
  checkSpreadsheetSize(bytes.byteLength);
  const entries: Record<string, Uint8Array<ArrayBuffer>> = Object.create(null);
  const names = new Set<string>();
  let inflatedBytes = 0;
  let xmlBytes = 0;
  let xmlNodes = 0;
  let sheetFiles = 0;
  let sheetDefinitions = 0;
  let cells = 0;
  let cellNodes = 0;
  let mergedCells = 0;
  let mergeRanges = 0;
  let parserExpandedCells = 0;

  const unzip = new Unzip((file) => {
    requireLimit(names.size < limits.archiveEntries);
    if (names.has(file.name) || file.name.startsWith("/") || file.name.includes("\\") || file.name.split("/").some((part) => part === "." || part === "..")) {
      throw new Error("Invalid workbook archive");
    }
    names.add(file.name);
    if (file.originalSize !== undefined) requireLimit(file.originalSize <= limits.entryBytes);
    const isSheet = /xl\/worksheets\/sheet\d+\.xml/.test(file.name);
    const isWorkbook = file.name === "xl/workbook.xml";
    const isXml = isSheet || /\.(?:xml|rels)$/.test(file.name) || parsedXmlPartNames.some((pattern) => pattern.test(file.name));
    if (isSheet) {
      sheetFiles += 1;
      requireLimit(sheetFiles <= limits.sheets);
    }
    const extent: Extent = { rows: 1, columns: 1 };
    const grow = (row: number, column: number) => {
      extent.rows = Math.max(extent.rows, row);
      extent.columns = Math.max(extent.columns, column);
      checkExtent(extent);
    };
    const range = (reference: string | undefined, expands = false) => {
      if (!reference) throw new Error("Invalid workbook range");
      const parsed = spreadsheetRange(reference);
      if (isSheet) grow(parsed.end.row, parsed.end.column);
      if (expands) {
        parserExpandedCells += parsed.cells;
        requireLimit(parserExpandedCells <= limits.parserExpandedCells);
      }
      return parsed;
    };
    const decoder = isXml ? new TextDecoder("utf-8", { fatal: true }) : undefined;
    const parser = isXml ? new SaxesParser() : undefined;
    let depth = 0;
    let namedRange: string | undefined;
    parser?.on("error", () => { throw new Error("Invalid workbook XML"); });
    parser?.on("doctype", () => { throw new Error("Invalid workbook XML"); });
    parser?.on("opentag", (node) => {
      depth += 1;
      xmlNodes += 1;
      requireLimit(depth <= limits.xmlDepth && xmlNodes <= limits.xmlNodes);
      const tag = node.name.split(":").at(-1);
      const attributes = node.attributes;
      if (isWorkbook && tag === "sheet") {
        sheetDefinitions += 1;
        requireLimit(sheetDefinitions <= limits.sheets);
        requireLimit(positiveInteger(attributes.sheetId) <= limits.sheetId);
      }
      if (isWorkbook && tag === "definedName") namedRange = "";
      if (!isSheet) return;
      switch (tag) {
        case "row": {
          const row = positiveInteger(attributes.r);
          requireLimit(row <= limits.rows);
          grow(row, 1);
          break;
        }
        case "c":
          cellNodes += 1;
          requireLimit(cellNodes <= limits.cellsPerWorkbook);
          range(attributes.r);
          break;
        case "col": {
          const min = positiveInteger(attributes.min);
          const max = positiveInteger(attributes.max);
          requireLimit(min <= max && max <= limits.columns);
          grow(1, max);
          break;
        }
        case "dimension":
        case "hyperlink":
          range(attributes.ref);
          break;
        case "mergeCell": {
          const parsed = range(attributes.ref);
          mergedCells += parsed.cells;
          mergeRanges += 1;
          requireLimit(parsed.cells <= limits.mergedCellsPerRange && mergedCells <= limits.mergedCellsPerWorkbook && mergeRanges <= limits.mergeRanges);
          break;
        }
        case "dataValidation":
          if (!attributes.sqref) throw new Error("Invalid workbook range");
          for (const reference of attributes.sqref.trim().split(/\s+/)) range(reference, true);
          break;
        case "f":
          if (attributes.ref) range(attributes.ref);
          break;
      }
    });
    const addNamedText = (text: string) => { if (namedRange !== undefined) namedRange += text; };
    parser?.on("text", addNamedText);
    parser?.on("cdata", addNamedText);
    parser?.on("closetag", (node) => {
      depth -= 1;
      if (node.name.split(":").at(-1) !== "definedName" || namedRange === undefined) return;
      // Defined names can contain comma-separated ranges and quoted sheet
      // names. Only cell references are expanded by ExcelJS; constants aren't.
      let quoted = false;
      let part = "";
      const checkName = () => {
        const reference = part.slice(part.lastIndexOf("!") + 1).trim();
        if (/\$\w+\$\d+/.test(reference)) range(reference, true);
        part = "";
      };
      for (const character of namedRange) {
        if (character === "'") quoted = !quoted;
        if (character === "," && !quoted) checkName();
        else part += character;
      }
      checkName();
      namedRange = undefined;
    });
    const chunks: Uint8Array[] = [];
    let entryBytes = 0;
    file.ondata = (error, chunk, final) => {
      if (error) throw error;
      entryBytes += chunk.byteLength;
      inflatedBytes += chunk.byteLength;
      requireLimit(entryBytes <= limits.entryBytes && inflatedBytes <= limits.inflatedBytes);
      if (parser && decoder) {
        xmlBytes += chunk.byteLength;
        requireLimit(xmlBytes <= limits.xmlBytes);
        parser.write(decoder.decode(chunk, { stream: !final }));
        if (final) parser.close();
      }
      chunks.push(chunk);
      if (final) {
        if (isSheet) {
          cells += checkExtent(extent);
          requireLimit(cells <= limits.cellsPerWorkbook);
        }
        const content = new Uint8Array(entryBytes);
        let offset = 0;
        for (const chunk of chunks) {
          content.set(chunk, offset);
          offset += chunk.byteLength;
        }
        entries[file.name] = content;
      }
    };
    file.start();
  });
  unzip.register(UnzipInflate);
  // Bound each inflater push as well as its cumulative output. A forged
  // originalSize must not give the inflater one enormous compressed chunk.
  let yieldedAt = performance.now();
  for (let offset = 0; offset < bytes.byteLength; offset += 1024) {
    unzip.push(bytes.subarray(offset, offset + 1024), offset + 1024 >= bytes.byteLength);
    if (performance.now() - yieldedAt > 8) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      yieldedAt = performance.now();
    }
  }
  if (!entries["xl/workbook.xml"] || names.size !== Object.keys(entries).length) throw new Error("Invalid workbook archive");
  // Optional document properties aren't displayed. ExcelJS assumes fixed
  // namespace prefixes here, so omit them from its input after every original
  // ZIP/XML entry has passed validation. The source bytes stay unchanged.
  delete entries["docProps/core.xml"];
  delete entries["docProps/app.xml"];
  return zipSync(entries, { level: 0 });
}

function cellDisplayTextLength(cell: Cell, richTextLengths: WeakMap<object, number>): number {
  const value = cell.value;
  if (value === null || value === undefined) return 0;
  // MergeValue displays its resolved value with toString(), including object
  // values. Match that existing display without joining a rich-text master.
  if (cell.isMerged && cell.master !== cell) return value.toString().length;
  if (typeof value !== "object" || value instanceof Date) return value.toString().length;
  if ("richText" in value) {
    const cached = richTextLengths.get(value);
    if (cached !== undefined) return cached;
    let length = 0;
    for (const run of value.richText) {
      length += run.text.length;
      requireLimit(length <= limits.displayTextPerCell);
    }
    richTextLengths.set(value, length);
    return length;
  }
  if ("text" in value) return value.text.length;
  if ("error" in value) return value.error.length;
  return value.result ? value.result.toString().length : 0;
}

export function checkParsedSpreadsheet(worksheets: Worksheet[]): void {
  requireLimit(worksheets.length <= limits.sheets);
  let cells = 0;
  let mergedCells = 0;
  let mergeRanges = 0;
  let displayText = 0;
  const richTextLengths = new WeakMap<object, number>();
  for (const worksheet of worksheets) {
    cells += checkExtent({ rows: Math.max(worksheet.rowCount, 1), columns: Math.max(worksheet.columnCount, 1) });
    requireLimit(cells <= limits.cellsPerWorkbook);
    for (const merge of worksheet.model.merges) {
      const parsed = spreadsheetRange(merge);
      mergedCells += parsed.cells;
      mergeRanges += 1;
      requireLimit(parsed.cells <= limits.mergedCellsPerRange && mergedCells <= limits.mergedCellsPerWorkbook && mergeRanges <= limits.mergeRanges);
    }
    // Visit only existing parsed cells, including merge slaves. Budget every
    // display occurrence across all sheets before any dense model or cell.text
    // read; shared rich-text lengths are measured once without concatenation.
    worksheet.eachRow((row) => row.eachCell((cell) => {
      const length = cellDisplayTextLength(cell, richTextLengths);
      requireLimit(Number.isSafeInteger(length) && length >= 0 && length <= limits.displayTextPerCell);
      displayText += length;
      requireLimit(displayText <= limits.displayTextPerWorkbook);
    }));
  }
}
