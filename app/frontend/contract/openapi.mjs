// Generate on stdout so callers can review/check the exact generated artifact.
import openapiTS, { astToString } from "openapi-typescript";
let input = "";
for await (const chunk of process.stdin) input += chunk;
process.stdout.write(astToString(await openapiTS(JSON.parse(input))));
