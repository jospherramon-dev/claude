-- Emulador mínimo del motor Quadcode Script (IQ Option) para probar el script.
-- Reproduce: ejecución 1 vez por vela, make_series persistente, get_value,
-- indexado de series [n], highest/lowest, input/input_group/instrument, plot/hline.

local engine = { bar = 0, plots = {}, hlines = {} }

local S = {}
S.__index = function(t, k)
    if type(k) == "number" then
        return setmetatable({ kind = "shift", src = t, n = k }, S)
    end
    return rawget(S, k)
end

function S.set(t, v)
    assert(t.kind == "store", "set() solo sobre series creadas con make_series")
    t.vals[engine.bar] = v
end

local function at(s, bar)
    if bar < 1 then return nil end
    if s.kind == "store" then
        return s.vals[bar]
    elseif s.kind == "shift" then
        return at(s.src, bar - s.n)
    elseif s.kind == "calc" then
        return s.fn(bar)
    end
end

local function newstore(name)
    return setmetatable({ kind = "store", name = name, vals = {} }, S)
end
local function newcalc(fn)
    return setmetatable({ kind = "calc", fn = fn }, S)
end

local is_series = function(x) return type(x) == "table" and getmetatable(x) == S end

-- ---------------------------------------------------------------- entorno
local env = {}
local persistent = {}          -- make_series persistente entre velas
local created_order = 0

env.make_series = function(name)
    created_order = created_order + 1
    local key = name or ("anon_" .. created_order)
    if not persistent[key] then persistent[key] = newstore(key) end
    return persistent[key]
end

env.get_value = function(x, def)
    if is_series(x) then
        local v = at(x, engine.bar)
        if v == nil then return def end
        return v
    end
    if x == nil then return def end
    return x
end

env.highest = function(s, n)
    return newcalc(function(bar)
        local m
        for i = 0, n - 1 do
            local v = at(s, bar - i)
            if v == nil then return nil end
            if m == nil or v > m then m = v end
        end
        return m
    end)
end

env.lowest = function(s, n)
    return newcalc(function(bar)
        local m
        for i = 0, n - 1 do
            local v = at(s, bar - i)
            if v == nil then return nil end
            if m == nil or v < m then m = v end
        end
        return m
    end)
end

env.instrument = function(t) engine.instrument = t end

env.input = setmetatable({
    integer = "integer", double = "double", boolean = "boolean", string = "string",
    color = "color", line_width = "line_width", plot_visibility = "plot_visibility",
    string_selection = "string_selection"
}, {
    __call = function(_, a, ...)
        if type(a) == "table" then return a.default end
        return a
    end
})

env.input_group = function(t)
    for k, v in pairs(t) do
        if type(k) == "string" then env[k] = v end     -- se inyectan como globales
    end
end

env.style   = { solid_line = "solid", dash_line = "dash", area = "area",
                points = "points", crosses = "crosses", levels = "levels" }
env.na_mode = { restart = "restart", continue = "continue" }

env.plot = function(series, name, color, width, offset, st, na)
    local v = env.get_value(series)
    engine.plots[name] = engine.plots[name] or {}
    engine.plots[name][engine.bar] = v and { v = v, c = color, w = width } or nil
end

env.hline = function(series, name, color, width)
    local v = env.get_value(series)
    if v then engine.hlines[name] = { v = v, c = color, w = width } end
end

env.rgba = function(r, g, b, a) return string.format("rgba(%d,%d,%d,%s)", r, g, b, tostring(a)) end
env.print = print
env.math, env.string, env.table, env.tostring, env.type, env.pairs, env.ipairs =
    math, string, table, tostring, type, pairs, ipairs
env.assert, env.error, env.tonumber, env.select = assert, error, tonumber, select
env.setmetatable, env.getmetatable, env.rawget = setmetatable, getmetatable, rawget

-- ------------------------------------------------------------- velas
local open_s, high_s, low_s, close_s = newstore("open"), newstore("high"), newstore("low"), newstore("close")
env.open, env.high, env.low, env.close = open_s, high_s, low_s, close_s

local function feed(bar, o, h, l, c)
    open_s.vals[bar], high_s.vals[bar], low_s.vals[bar], close_s.vals[bar] = o, h, l, c
end

-- ------------------------------------------------------------- runner
local function run(path, candles)
    local chunk = assert(loadfile(path, "t", env))
    for i, k in ipairs(candles) do
        feed(i, k[1], k[2], k[3], k[4])
        engine.bar = i
        local ok, err = pcall(chunk)
        if not ok then
            error(("ERROR en la vela %d: %s"):format(i, tostring(err)))
        end
    end
    return engine
end

return { run = run, engine = engine }
