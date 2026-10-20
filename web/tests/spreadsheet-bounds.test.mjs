import test from "node:test";
import assert from "node:assert/strict";
import ExcelJS from "exceljs";
import JSZip from "jszip";
import "./typescript-loader.mjs";
import { loadSpreadsheet } from "../src/components/citations/preview/xlsx/spreadsheet-model.ts";
import { checkParsedSpreadsheet, preflightSpreadsheet } from "../src/components/citations/preview/xlsx/spreadsheet-bounds.ts";

const limitMessage = "This workbook exceeds preview limits.";
const parserPrototype = Object.getPrototypeOf(new ExcelJS.Workbook().xlsx);
const ordinaryData = '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Generated</t></is></c></row></sheetData>';
const cellPrototype = Object.getPrototypeOf(new ExcelJS.Workbook().addWorksheet("Generated").getCell("A1"));

async function workbookBlob(sheets = [ordinaryData], extras = {}, definedNames = "") {
  const zip = new JSZip();
  zip.file("[Content_Types].xml", `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${sheets.map((_, index) => `<Override PartName="/xl/worksheets/sheet${index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}</Types>`, { createFolders: false });
  zip.file("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>', { createFolders: false });
  zip.file("xl/workbook.xml", `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((_, index) => `<sheet name="Generated ${index + 1}" sheetId="${index + 1}" r:id="rId${index + 1}"/>`).join("")}</sheets>${definedNames}</workbook>`, { createFolders: false });
  zip.file("xl/_rels/workbook.xml.rels", `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_, index) => `<Relationship Id="rId${index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${index + 1}.xml"/>`).join("")}</Relationships>`, { createFolders: false });
  sheets.forEach((body, index) => zip.file(`xl/worksheets/sheet${index + 1}.xml`, `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">${body}</worksheet>`, { createFolders: false }));
  for (const [name, content] of Object.entries(extras)) zip.file(name, content, { createFolders: false });
  return new Blob([await zip.generateAsync({ type: "uint8array", compression: "DEFLATE" })]);
}

function sparseCell(address) {
  const row = address.match(/\d+$/)[0];
  return `<sheetData><row r="${row}"><c r="${address}" t="inlineStr"><is><t>Generated</t></is></c></row></sheetData>`;
}

// The pre-fix parser would apply huge merges and the preview would allocate a
// dense grid. Stop at that boundary even on the red run; never execute them.
async function refusesBeforeParser(t, blob, message = limitMessage) {
  const stopped = t.mock.method(parserPrototype, "load", async () => {
    throw new Error("Unsafe parser reached in bounded regression test");
  });
  await assert.rejects(loadSpreadsheet(blob), { name: "Error", message });
  assert.equal(stopped.mock.callCount(), 0);
}

function sharedTextSheet(count) {
  return `<sheetData>${Array.from({ length: count }, (_, index) => `<row r="${index + 1}"><c r="A${index + 1}" t="s"><v>0</v></c></row>`).join("")}</sheetData>`;
}

function sharedTextPart(text, rich = false) {
  const midpoint = Math.floor(text.length / 4) * 2;
  const xmlText = (part) => part.replaceAll("😀", "&#x1F600;");
  const content = rich
    ? `<r><t>${xmlText(text.slice(0, midpoint))}</t></r><r><t>${xmlText(text.slice(midpoint))}</t></r>`
    : `<t>${xmlText(text)}</t>`;
  return `<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si>${content}</si></sst>`;
}

// Keep red runs small: the real parser may resolve repeated references, but
// stop before dense getCell or cell.text can join any repeated rich strings.
async function refusesBeforeDisplayModel(t, blob) {
  const textRead = t.mock.getter(cellPrototype, "text", () => {
    throw new Error("Display text reached in bounded regression test");
  });
  const denseReads = [];
  const original = parserPrototype.load;
  t.mock.method(parserPrototype, "load", async function (...args) {
    const result = await original.apply(this, args);
    for (const sheet of this.workbook.worksheets) {
      denseReads.push(t.mock.method(sheet, "getCell", () => {
        throw new Error("Dense model reached in bounded regression test");
      }));
    }
    return result;
  });
  await assert.rejects(loadSpreadsheet(blob), { name: "Error", message: limitMessage });
  assert.equal(textRead.mock.callCount(), 0);
  for (const read of denseReads) assert.equal(read.mock.callCount(), 0);
}

for (const [name, body, extras] of [
  ["inline string", `<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>${"x".repeat(32769)}</t></is></c></row></sheetData>`, {}],
  ["shared string", sharedTextSheet(1), { "xl/sharedStrings.xml": sharedTextPart("x".repeat(32769)) }],
  ["shared rich text", sharedTextSheet(1), { "xl/sharedStrings.xml": sharedTextPart("x".repeat(32769), true) }],
  ["cached formula string", `<sheetData><row r="1"><c r="A1" t="str"><f>"Generated"</f><v>${"x".repeat(32769)}</v></c></row></sheetData>`, {}],
  ["UTF-16 rich text", sharedTextSheet(1), { "xl/sharedStrings.xml": sharedTextPart("😀".repeat(16385), true) }],
]) {
  test(`per-cell ${name} is refused before dense construction or display-text expansion`, async (t) => {
    const blob = await workbookBlob([body], extras);
    assert.ok(blob.size < 10000);
    await refusesBeforeDisplayModel(t, blob);
  });
}

test("per-cell hyperlink labels have the same display-text budget", async (t) => {
  const workbook = new ExcelJS.Workbook();
  workbook.addWorksheet("Generated link").getCell("A1").value = {
    text: "x".repeat(32769), hyperlink: "https://generated.example/source",
  };
  const blob = new Blob([await workbook.xlsx.writeBuffer()]);
  assert.ok(blob.size < 10000);
  await refusesBeforeDisplayModel(t, blob);
});

for (const rich of [false, true]) {
  test(`shared ${rich ? "rich text" : "strings"} at the per-cell UTF-16 boundary retain all text`, async () => {
    const blob = await workbookBlob([sharedTextSheet(1)], { "xl/sharedStrings.xml": sharedTextPart("😀".repeat(16384), rich) });
    const sheets = await loadSpreadsheet(blob);
    assert.equal(sheets[0].cells[0][0].text, "😀".repeat(16384));
    assert.equal(sheets[0].cells[0][0].text.length, 32768);
  });

  test(`repeated shared ${rich ? "rich text" : "strings"} use an aggregate workbook display budget`, async (t) => {
    // 256 references contain exactly 8 Mi UTF-16 units. The earlier ordinary
    // sheet adds nine units, and must not build before a later sheet refuses.
    const blob = await workbookBlob([ordinaryData, sharedTextSheet(128), sharedTextSheet(128)], {
      "xl/sharedStrings.xml": sharedTextPart("x".repeat(32768), rich),
    });
    assert.ok(blob.size < 10000);
    await refusesBeforeDisplayModel(t, blob);
  });

  test(`aggregate shared ${rich ? "rich text" : "strings"} exactly at the workbook boundary are accepted without joining`, async (t) => {
    const blob = await workbookBlob([sharedTextSheet(128), sharedTextSheet(128)], {
      "xl/sharedStrings.xml": sharedTextPart("x".repeat(32768), rich),
    });
    const bytes = await preflightSpreadsheet(new Uint8Array(await blob.arrayBuffer()));
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.load(bytes.buffer);
    const textRead = t.mock.getter(cellPrototype, "text", () => {
      throw new Error("Guard expanded display text");
    });
    assert.doesNotThrow(() => checkParsedSpreadsheet(workbook.worksheets));
    assert.equal(workbook.worksheets.length, 2);
    assert.equal(workbook.worksheets[0].rowCount, 128);
    assert.equal(workbook.worksheets[1].rowCount, 128);
    assert.equal(textRead.mock.callCount(), 0);
  });
}

test("merged string slaves count toward the aggregate dense display-text budget", async (t) => {
  const blob = await workbookBlob([sharedTextSheet(1) + '<mergeCells count="1"><mergeCell ref="A1:A257"/></mergeCells>'], {
    "xl/sharedStrings.xml": sharedTextPart("x".repeat(32768)),
  });
  assert.ok(blob.size < 10000);
  await refusesBeforeDisplayModel(t, blob);
});

test("merged rich text preserves ExcelJS's existing slave display without false expansion", async () => {
  const blob = await workbookBlob([sharedTextSheet(1) + '<mergeCells count="1"><mergeCell ref="A1:A257"/></mergeCells>'], {
    "xl/sharedStrings.xml": sharedTextPart("x".repeat(32768), true),
  });
  const sheets = await loadSpreadsheet(blob);
  assert.equal(sheets[0].cells.length, 257);
  assert.equal(sheets[0].cells[0][0].text, "x".repeat(32768));
  assert.equal(sheets[0].cells[1][0].text, "[object Object]");
  assert.equal(sheets[0].cells.flat().reduce((total, cell) => total + cell.text.length, 0), 36608);
});

for (const count of [8, 32]) {
  test(`${count} references to one small rich string retain bounded ordinary content`, async (t) => {
    const blob = await workbookBlob([sharedTextSheet(count)], {
      "xl/sharedStrings.xml": sharedTextPart("x".repeat(1024), true),
    });
    const sheets = await loadSpreadsheet(blob);
    assert.equal(sheets[0].cells.length, count);
    assert.equal(sheets[0].cells.flat().reduce((total, cell) => total + cell.text.length, 0), count === 8 ? 8192 : 32768);
    t.diagnostic(`Generated archive bytes=${blob.size}; displayed UTF-16 units=${count === 8 ? 8192 : 32768}`);
  });
}

test("extreme finite row heights stay within a finite preview dimension", async () => {
  const body = '<sheetData><row r="1" ht="1e9"><c r="A1" t="inlineStr"><is><t>Generated</t></is></c></row><row r="2" ht="409.5"><c r="A2" t="inlineStr"><is><t>Generated</t></is></c></row></sheetData>';
  const sheets = await loadSpreadsheet(await workbookBlob([body]));
  assert.deepEqual(sheets[0].rowHeights, [546, 546]);
});

test("nonfinite row heights fall back to the ordinary finite preview height", async () => {
  const body = `<sheetData>${["Infinity", "-Infinity", "NaN"].map((height, index) => `<row r="${index + 1}" ht="${height}"><c r="A${index + 1}" t="inlineStr"><is><t>Generated</t></is></c></row>`).join("")}</sheetData>`;
  const sheets = await loadSpreadsheet(await workbookBlob([body]));
  assert.deepEqual(sheets[0].rowHeights, [20, 20, 20]);
});

test("tiny valid far-coordinate XLSX is refused before ExcelJS load", async (t) => {
  const blob = await workbookBlob([sparseCell("XFD1048576")]);
  assert.ok(blob.size < 2500);
  await refusesBeforeParser(t, blob);
});

for (const [name, body] of [
  ["row extent", sparseCell("A2001")],
  ["column extent", sparseCell("CW1")],
  ["per-sheet dense area", sparseCell("M2000")],
  ["styled empty row", '<sheetData><row r="1048576" s="1"/></sheetData>'],
  ["column definition expansion", '<cols><col min="1" max="16384" width="12"/></cols>' + ordinaryData],
  ["worksheet dimension", '<dimension ref="A1:XFD1048576"/>' + ordinaryData],
]) {
  test(`${name} is bounded before ExcelJS load`, async (t) => {
    await refusesBeforeParser(t, await workbookBlob([body]));
  });
}

test("aggregate dense cells are checked across all sheets before parser load", async (t) => {
  await refusesBeforeParser(t, await workbookBlob(Array(3).fill(sparseCell("J2000"))));
});

test("a merge within worksheet dimensions still has a finite span budget", async (t) => {
  await refusesBeforeParser(t, await workbookBlob([ordinaryData + '<mergeCells count="1"><mergeCell ref="A1:Z100"/></mergeCells>']));
});

test("merged spans also have an aggregate workbook budget", async (t) => {
  await refusesBeforeParser(t, await workbookBlob(Array(6).fill(ordinaryData + '<mergeCells count="1"><mergeCell ref="A1:T100"/></mergeCells>')));
});

test("data-validation ranges cannot expand an unbounded parser model", async (t) => {
  await refusesBeforeParser(t, await workbookBlob([ordinaryData + '<dataValidations count="1"><dataValidation type="whole" sqref="A1:XFD1048576"><formula1>1</formula1></dataValidation></dataValidations>']));
});

test("named ranges cannot expand an unbounded parser matrix", async (t) => {
  await refusesBeforeParser(t, await workbookBlob([ordinaryData], {}, '<definedNames><definedName name="GeneratedRange">\'Generated 1\'!$A$1:$XFD$1048576</definedName></definedNames>'));
});

test("archive entry count is bounded before parser load", async (t) => {
  const extras = Object.fromEntries(Array.from({ length: 252 }, (_, i) => [`generated/${i}.bin`, "x"]));
  await refusesBeforeParser(t, await workbookBlob([ordinaryData], extras));
});

test("actual inflated entry bytes are bounded even with a forged ZIP size", async (t) => {
  const blob = await workbookBlob([ordinaryData], { "generated.bin": "x".repeat(8 * 1024 * 1024 + 1) });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const view = new DataView(bytes.buffer);
  const decoder = new TextDecoder();
  for (let offset = 0; offset < bytes.length - 46; offset += 1) {
    const signature = view.getUint32(offset, true);
    if (signature === 0x04034b50 && decoder.decode(bytes.subarray(offset + 30, offset + 30 + view.getUint16(offset + 26, true))) === "generated.bin") view.setUint32(offset + 22, 1, true);
    if (signature === 0x02014b50 && decoder.decode(bytes.subarray(offset + 46, offset + 46 + view.getUint16(offset + 28, true))) === "generated.bin") view.setUint32(offset + 24, 1, true);
  }
  assert.ok(bytes.length < 15000);
  await refusesBeforeParser(t, new Blob([bytes]));
});

test("aggregate inflated bytes are bounded across archive entries", async (t) => {
  const extras = Object.fromEntries(Array.from({ length: 5 }, (_, i) => [`generated-${i}.bin`, "x".repeat(7 * 1024 * 1024)]));
  await refusesBeforeParser(t, await workbookBlob([ordinaryData], extras));
});

test("XML nesting is bounded before ExcelJS constructs objects", async (t) => {
  await refusesBeforeParser(t, await workbookBlob(["<generated>".repeat(65) + "</generated>".repeat(65)]));
});

test("XML node count is bounded before ExcelJS constructs objects", async (t) => {
  await refusesBeforeParser(t, await workbookBlob(["<generated/>".repeat(250001)]));
});

const deeplyNestedXml = "<xml>" + "<generated>".repeat(65) + "</generated>".repeat(65) + "</xml>";

for (const name of ["docProps/core.xml", "docProps/app.xml"]) {
  for (const [condition, content, message] of [
    ["nesting", deeplyNestedXml, limitMessage],
    ["inflated bytes", "<xml>" + "x".repeat(8 * 1024 * 1024) + "</xml>", limitMessage],
    ["DOCTYPE", "<!DOCTYPE xml><xml/>", "Workbook preview failed"],
  ]) {
    test(`optional properties ${name} ${condition} are checked before parser load`, async (t) => {
      await refusesBeforeParser(t, await workbookBlob([ordinaryData], { [name]: content }), message);
    });
  }
}

test("VML XML nesting is bounded before ExcelJS's eager VML parser", async (t) => {
  await refusesBeforeParser(t, await workbookBlob([ordinaryData], { "xl/drawings/vmlDrawing1.vml": deeplyNestedXml }));
});

test("VML XML nodes use the same aggregate XML budget", async (t) => {
  await refusesBeforeParser(t, await workbookBlob([ordinaryData], { "xl/drawings/vmlDrawing1.vml": "<xml>" + "<generated/>".repeat(250001) + "</xml>" }));
});

test("VML XML bytes use the same aggregate XML budget", async (t) => {
  const extras = Object.fromEntries(Array.from({ length: 4 }, (_, i) => [`xl/drawings/vmlDrawing${i + 1}.vml`, "<xml>" + "x".repeat(7 * 1024 * 1024) + "</xml>"]));
  await refusesBeforeParser(t, await workbookBlob([ordinaryData], extras));
});

test("VML DOCTYPE is refused before the eager parser with a safe message", async (t) => {
  await refusesBeforeParser(t, await workbookBlob([ordinaryData], { "xl/drawings/vmlDrawing1.vml": '<!DOCTYPE xml><xml/>' }), "Workbook preview failed");
});

for (const name of [
  "xl/worksheets/_rels/sheet1.xml.rels.backup",
  "xl/theme/theme1.xml.backup",
  "xl/drawings/drawing1.xml.backup",
  "xl/comments1.xml.backup",
  "xl/tables/table1.xml.backup",
  "xl/drawings/_rels/drawing1.xml.rels.backup",
  "xl/drawings/vmlDrawing1.vml.backup",
]) {
  test(`ExcelJS-parsed XML part ${name} stays bounded with a trailing suffix`, async (t) => {
    await refusesBeforeParser(t, await workbookBlob([ordinaryData], { [name]: deeplyNestedXml }));
  });
}

test("ordinary comment VML still passes the real eager parser and retains the worksheet", async (t) => {
  const original = parserPrototype._processVmlDrawingEntry;
  const parsed = t.mock.method(parserPrototype, "_processVmlDrawingEntry", async function (...args) {
    return original.apply(this, args);
  });
  const workbook = new ExcelJS.Workbook();
  workbook.addWorksheet("Generated VML").getCell("A1").value = "Generated cell";
  workbook.worksheets[0].getCell("A1").note = "Generated ordinary note";
  const sheets = await loadSpreadsheet(new Blob([await workbook.xlsx.writeBuffer()]));
  assert.equal(parsed.mock.callCount(), 1);
  assert.equal(sheets.length, 1);
  assert.equal(sheets[0].name, "Generated VML");
  assert.equal(sheets[0].columnCount, 1);
  assert.equal(sheets[0].cells.length, 1);
  assert.equal(sheets[0].cells[0][0].text, "Generated cell");
});

for (const date1904 of [false, true]) {
  test(`alternative core property namespace preserves preview content (date1904=${date1904})`, async () => {
    const workbook = new ExcelJS.Workbook();
    workbook.properties.date1904 = date1904;
    const sheet = workbook.addWorksheet("Generated namespace aliases");
    sheet.getCell("A1").value = "Generated title";
    sheet.getCell("A1").font = { bold: true, color: { argb: "FF123456" } };
    sheet.getCell("A1").fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFEEDDCC" } };
    sheet.getCell("A2").value = 2;
    sheet.getCell("B2").value = { formula: "A2+1", result: 3 };
    sheet.getCell("A3").value = { text: "Generated link", hyperlink: "https://generated.example/source" };
    const date = new Date("2026-10-19T00:00:00.000Z");
    sheet.getCell("B3").value = date;
    const zip = await JSZip.loadAsync(await workbook.xlsx.writeBuffer());
    const core = await zip.file("docProps/core.xml").async("string");
    zip.file("docProps/core.xml", core.replaceAll("xmlns:cp=", "xmlns:ns0=").replaceAll("cp:", "ns0:"));
    const bytes = await zip.generateAsync({ type: "uint8array", compression: "DEFLATE" });
    assert.ok(bytes.length < 10000);
    const original = bytes.slice();
    const sheets = await loadSpreadsheet(new Blob([bytes]));
    assert.equal(sheets.length, 1);
    assert.equal(sheets[0].name, "Generated namespace aliases");
    assert.equal(sheets[0].columnCount, 2);
    assert.equal(sheets[0].cells.length, 3);
    assert.equal(sheets[0].cells[0][0].text, "Generated title");
    assert.equal(sheets[0].cells[0][0].style.fontWeight, 700);
    assert.equal(sheets[0].cells[0][0].style.color, "rgba(18, 52, 86, 1)");
    assert.equal(sheets[0].cells[0][0].style.background, "rgba(238, 221, 204, 1)");
    assert.equal(sheets[0].cells[1][0].text, "2");
    assert.equal(sheets[0].cells[1][1].formula, "A2+1");
    assert.equal(sheets[0].cells[1][1].text, "3");
    assert.equal(sheets[0].cells[2][0].hyperlink, "https://generated.example/source");
    assert.equal(sheets[0].cells[2][0].text, "Generated link");
    assert.equal(sheets[0].cells[2][1].text, date.toString());
    const parserBytes = await preflightSpreadsheet(bytes);
    assert.deepEqual(bytes, original);
    const parsed = new ExcelJS.Workbook();
    await parsed.xlsx.load(parserBytes.buffer);
    assert.equal(parsed.properties.date1904, date1904);
    assert.equal(parsed.worksheets[0].getCell("B3").value.toISOString(), "2026-10-19T00:00:00.000Z");
  });
}

test("alternative app property namespace still permits worksheet preview", async () => {
  const blob = await workbookBlob([ordinaryData], {
    "docProps/app.xml": '<ns1:Properties xmlns:ns1="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><ns1:Application>Generated</ns1:Application></ns1:Properties>',
  });
  const sheets = await loadSpreadsheet(blob);
  assert.equal(sheets.length, 1);
  assert.equal(sheets[0].cells[0][0].text, "Generated");
});

test("malformed archive is reported through a safe preview error", async () => {
  await assert.rejects(loadSpreadsheet(new Blob(["generated invalid ZIP"])), { name: "Error", message: "Workbook preview failed" });
});

test("post-parse extents are checked before the first dense getCell", async (t) => {
  let expanded = false;
  t.mock.method(parserPrototype, "load", async function () {
    const sheet = this.workbook.addWorksheet("Generated");
    Object.defineProperty(sheet, "rowCount", { value: 2001 });
    sheet.getCell = () => { expanded = true; throw new Error("Dense grid reached"); };
  });
  await assert.rejects(loadSpreadsheet(await workbookBlob()), { name: "Error", message: limitMessage });
  assert.equal(expanded, false);
});

test("ordinary XLSX retains cells, formatting, merges, formulas and links after refusal", async () => {
  await assert.rejects(loadSpreadsheet(new Blob(["generated invalid ZIP"])));
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Generated ordinary");
  sheet.getCell("A1").value = "Title";
  sheet.getCell("A1").font = { name: "Arial", size: 12, bold: true, color: { argb: "FF123456" } };
  sheet.getCell("A1").fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFEEDDCC" } };
  sheet.getCell("A1").alignment = { horizontal: "center", vertical: "middle", wrapText: true };
  sheet.getCell("A1").border = { bottom: { style: "thin", color: { argb: "FF000000" } } };
  sheet.mergeCells("A1:B1");
  sheet.getCell("A2").value = { formula: "1+2", result: 3 };
  sheet.getCell("B2").value = { text: "Generated link", hyperlink: "https://generated.example/source" };
  sheet.getCell("A3").value = "Repeated shared text";
  sheet.getCell("B3").value = "Repeated shared text";
  sheet.getCell("A4").value = { richText: [{ font: { bold: true }, text: "Rich " }, { text: "text" }] };
  const date = new Date("2026-10-19T00:00:00.000Z");
  sheet.getCell("B4").value = date;
  sheet.getColumn(1).width = 20;
  sheet.getRow(1).height = 30;
  const bytes = new Uint8Array(await workbook.xlsx.writeBuffer());
  const original = bytes.slice();
  const sheets = await loadSpreadsheet(new Blob([bytes]));
  assert.equal(sheets.length, 1);
  assert.equal(sheets[0].name, "Generated ordinary");
  assert.equal(sheets[0].columnCount, 2);
  assert.equal(sheets[0].cells.length, 4);
  assert.equal(sheets[0].cells[0][0].text, "Title");
  assert.deepEqual(sheets[0].merges.get("1:1"), { master: true, rowSpan: 1, columnSpan: 2 });
  assert.deepEqual(sheets[0].merges.get("1:2"), { master: false, rowSpan: 1, columnSpan: 2 });
  assert.equal(sheets[0].cells[1][0].formula, "1+2");
  assert.equal(sheets[0].cells[1][0].text, "3");
  assert.equal(sheets[0].cells[1][1].hyperlink, "https://generated.example/source");
  assert.equal(sheets[0].cells[1][1].text, "Generated link");
  assert.equal(sheets[0].cells[2][0].text, "Repeated shared text");
  assert.equal(sheets[0].cells[2][1].text, "Repeated shared text");
  assert.equal(sheets[0].cells[3][0].text, "Rich text");
  assert.equal(sheets[0].cells[3][1].text, date.toString());
  assert.equal(sheets[0].columnWidths[0], 148);
  assert.equal(sheets[0].rowHeights[0], 40);
  assert.equal(sheets[0].cells[0][0].style.background, "rgba(238, 221, 204, 1)");
  assert.equal(sheets[0].cells[0][0].style.color, "rgba(18, 52, 86, 1)");
  assert.equal(sheets[0].cells[0][0].style.fontWeight, 700);
  assert.equal(sheets[0].cells[0][0].style.fontFamily, "Arial");
  assert.equal(sheets[0].cells[0][0].style.fontSize, "12pt");
  assert.equal(sheets[0].cells[0][0].style.textAlign, "center");
  assert.equal(sheets[0].cells[0][0].style.verticalAlign, "middle");
  assert.equal(sheets[0].cells[0][0].style.whiteSpace, "normal");
  assert.equal(sheets[0].cells[0][0].style.borderBottom, "1px solid rgba(0, 0, 0, 1)");
  const parserBytes = await preflightSpreadsheet(bytes);
  assert.deepEqual(bytes, original);
  const parsed = new ExcelJS.Workbook();
  await parsed.xlsx.load(parserBytes.buffer);
  assert.deepEqual(parsed.worksheets[0].getCell("A4").value, { richText: [{ font: { bold: true }, text: "Rich " }, { text: "text" }] });
  assert.equal(parsed.worksheets[0].getCell("B4").value.toISOString(), "2026-10-19T00:00:00.000Z");
});
