local ADDON, ns = ...

-- Empty schema-1 plan. Used when PlanData.lua is missing or invalid.
ForeverGoldPlanDefault = {
  ["schema"] = 1,
  ["id"] = "none",
  ["generatedAt"] = 0,
  ["basedOnScan"] = { ["at"] = 0, ["market"] = "", ["rows"] = 0 },
  ["lastScanAt"] = 0,
  ["expectPriorSessions"] = 0,
  ["settings"] = { ["postMode"] = "match", ["undercut"] = 100 },
  ["headline"] = "No plan yet: run /forever-gold in Claude Code, then /reload.",
  ["notes"] = {},
  ["steps"] = {},
  ["budget"] = { ["spendCap"] = 0, ["principal"] = 0 },
  ["buy"] = {},
  ["craft"] = {},
  ["post"] = {},
  ["hold"] = {},
  ["vendor"] = {},
  ["ref"] = {},
}
