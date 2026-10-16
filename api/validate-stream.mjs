import { EventSchemas } from "@ag-ui/core";

let input = "";
for await (const chunk of process.stdin) input += chunk;
let events = [];
let valid = false;
try {
  events = JSON.parse(input);
  valid = Array.isArray(events) && events.every(event => EventSchemas.safeParse(event).success);
} catch {}
process.stdout.write(JSON.stringify({ events: events.length, valid }) + "\n");
process.exitCode = valid ? 0 : 1;
