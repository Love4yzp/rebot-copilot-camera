/** What is selected on the editing track, in either density. */
export type Selection =
  | { kind: "block"; id: string }
  | { kind: "marker"; blockId: string; markerId: string }
  | null;
