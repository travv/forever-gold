-- Writes a global the way the WoW client writes SavedVariables:
--   Name = {
--   	["key"] = value,
--   	value, -- [1]
--   }
-- Keys are sorted (numbers, then strings) so harness fixtures are deterministic.

local SV = {}

local function num(n)
  if n ~= n or n == math.huge or n == -math.huge then
    error("svwriter: cannot write the number " .. tostring(n))
  end
  if n == math.floor(n) and math.abs(n) < 2 ^ 53 then
    return string.format("%.0f", n)
  end
  local s = string.format("%.14g", n)
  if tonumber(s) ~= n then s = string.format("%.17g", n) end
  return s
end

local function str(s)
  s = s:gsub("\\", "\\\\"):gsub("\"", "\\\""):gsub("\n", "\\n"):gsub("\r", "\\r")
  return "\"" .. s .. "\""
end
SV.quote = str

local KIND = { number = 1, string = 2, boolean = 3 }

local function keyLess(a, b)
  local ta, tb = type(a), type(b)
  if ta ~= tb then return (KIND[ta] or 9) < (KIND[tb] or 9) end
  if ta == "boolean" then return (not a) and b end
  return a < b
end

local function keyText(k)
  local t = type(k)
  if t == "string" then return "[" .. str(k) .. "]" end
  if t == "number" then return "[" .. num(k) .. "]" end
  if t == "boolean" then return "[" .. tostring(k) .. "]" end
  error("svwriter: cannot write a key of type " .. t)
end

local function write(out, v, indent, seen)
  local t = type(v)
  if t == "string" then
    out[#out + 1] = str(v)
  elseif t == "number" then
    out[#out + 1] = num(v)
  elseif t == "boolean" then
    out[#out + 1] = tostring(v)
  elseif t == "table" then
    if seen[v] then error("svwriter: table cycle") end
    seen[v] = true
    out[#out + 1] = "{\n"
    local inner = indent .. "\t"
    local n = 0
    while v[n + 1] ~= nil do n = n + 1 end
    for i = 1, n do
      out[#out + 1] = inner
      write(out, v[i], inner, seen)
      out[#out + 1] = ", -- [" .. num(i) .. "]\n"
    end
    local keys = {}
    for k in pairs(v) do
      if not (type(k) == "number" and k >= 1 and k <= n and k == math.floor(k)) then keys[#keys + 1] = k end
    end
    table.sort(keys, keyLess)
    for _, k in ipairs(keys) do
      out[#out + 1] = inner .. keyText(k) .. " = "
      write(out, v[k], inner, seen)
      out[#out + 1] = ",\n"
    end
    out[#out + 1] = indent .. "}"
    seen[v] = nil
  else
    error("svwriter: cannot write a value of type " .. t)
  end
end

function SV.serialize(name, value)
  local out = { "\n", name, " = " }
  if value == nil then
    out[#out + 1] = "nil"
  else
    write(out, value, "", {})
  end
  out[#out + 1] = "\n"
  return table.concat(out)
end

local function mkdirs(dir)
  if package.config:sub(1, 1) == "\\" then
    os.execute("mkdir \"" .. dir:gsub("/", "\\") .. "\" >nul 2>nul")
  else
    os.execute("mkdir -p '" .. dir:gsub("'", "'\\''") .. "'")
  end
end
SV.mkdirs = mkdirs

-- Binary mode: LF line endings on every OS. Like the client, keeps a .bak.
function SV.writeFile(path, text, keepBak)
  mkdirs(path:match("^(.*)[/\\][^/\\]+$") or ".")
  if keepBak then
    local old = io.open(path, "rb")
    if old then
      local prev = old:read("*a")
      old:close()
      local bak = assert(io.open(path .. ".bak", "wb"))
      bak:write(prev)
      bak:close()
    end
  end
  local f = assert(io.open(path, "wb"))
  f:write(text)
  f:close()
  return #text
end

function SV.write(path, name, value, keepBak)
  return SV.writeFile(path, SV.serialize(name, value), keepBak)
end

-- The client's read-back: run the file in an empty environment, take the global.
function SV.load(text, name)
  local chunk = assert(loadstring(text, "=SavedVariables"))
  local env = {}
  setfenv(chunk, env)
  chunk()
  return env[name]
end

function SV.readFile(path, name)
  local f = io.open(path, "rb")
  if not f then return nil end
  local text = f:read("*a")
  f:close()
  return SV.load(text, name), text
end

return SV
