-- =====================================================================
--  Pruebas del indicador soportes_resistencias_fuerza.lua
--  Se ejecuta el script sobre un emulador del motor de IQ Option
--  (qcs_mock.lua), vela a vela, con datos sintéticos.
--
--      cd scripts_iqoption/pruebas && lua5.3 test_sr.lua
-- =====================================================================

local aqui  = (arg and arg[0] or ""):match("^(.*)[/\\]") or "."
local MOCK  = aqui .. "/qcs_mock.lua"
local RUTA  = aqui .. "/../soportes_resistencias_fuerza.lua"

local ROJO, VERDE, AMARILLO = "#FF3B30", "#25E154", "#FFD400"

local fallos = 0
local function comprobar(texto, ok)
    print(("  [%s] %s"):format(ok and "OK " or "MAL", texto))
    if not ok then fallos = fallos + 1 end
end

-- Ejecuta el indicador sobre unas velas y devuelve el motor emulado.
local function correr(candles)
    local mock = dofile(MOCK)                -- motor limpio en cada escenario
    local ok, err = pcall(mock.run, RUTA, candles)
    if not ok then
        print("  [MAL] el script lanzó un error: " .. tostring(err))
        fallos = fallos + 1
        return nil
    end
    return mock.engine
end

local function lineas_usadas(engine, n_velas)
    local usadas, puntos = 0, 0
    for _, serie in pairs(engine.plots) do
        local barras = 0
        for b = 1, n_velas do if serie[b] then barras = barras + 1 end end
        if barras > 0 then usadas = usadas + 1 end
        puntos = puntos + barras
    end
    return usadas, puntos
end

-- Un cambio grande de precio dentro de una misma línea debe venir siempre
-- precedido de un hueco: si no, se vería un salto en diagonal en el gráfico.
local function saltos_sin_hueco(engine, n_velas)
    local saltos = 0
    for _, serie in pairs(engine.plots) do
        local anterior = nil
        for b = 1, n_velas do
            local p = serie[b]
            if p then
                if anterior and math.abs(p.v - anterior) > 0.001 * math.abs(p.v) then
                    saltos = saltos + 1
                end
                anterior = p.v
            else
                anterior = nil
            end
        end
    end
    return saltos
end

-- ---------------------------------------------------------------------
-- 1) Rango con rebotes repetidos: soporte y resistencia claros
-- ---------------------------------------------------------------------
print("1) Rango 1.1000 / 1.1050 con 12 recorridos y ruptura alcista")
math.randomseed(7)

local SOP, RES = 1.1000, 1.1050
local rango, precio, dir = {}, (SOP + RES) / 2, 1
for _ = 1, 12 do
    local objetivo = (dir == 1) and RES or SOP
    while (dir == 1 and precio < objetivo - 0.0004) or (dir == -1 and precio > objetivo + 0.0004) do
        local o = precio
        local c = o + (0.0004 + math.random() * 0.0004) * dir
        rango[#rango + 1] = { o, math.max(o, c) + math.random() * 0.00015,
                                 math.min(o, c) - math.random() * 0.00015, c }
        precio = c
    end
    local o = precio                                   -- vela de rechazo con mecha
    local c = objetivo - 0.0009 * dir
    if dir == 1 then
        rango[#rango + 1] = { o, RES + 0.00008, math.min(o, c) - 0.00005, c }
    else
        rango[#rango + 1] = { o, math.max(o, c) + 0.00005, SOP - 0.00008, c }
    end
    precio, dir = c, -dir
end

local barras_rango = #rango
for _ = 1, 40 do                                       -- ruptura alcista limpia
    local o = precio
    local c = o + 0.0006
    rango[#rango + 1] = { o, c + 0.0001, o - 0.0001, c }
    precio = c
end

local engine = correr(rango)
if engine then
    local cerca = function(a, b) return math.abs(a - b) < 0.0015 end
    local res_marcada, sop_marcado, res_roja, sop_rojo = false, false, false, false
    local res_viva, giro = false, false

    for nombre, serie in pairs(engine.plots) do
        local lado = nombre:sub(1, 1)
        for b = 1, #rango do
            local p = serie[b]
            if p then
                if lado == "R" and cerca(p.v, RES) and b <= barras_rango then
                    res_marcada = true
                    if p.c == ROJO then res_roja = true end
                end
                if lado == "S" and cerca(p.v, SOP) then
                    sop_marcado = true
                    if p.c == ROJO then sop_rojo = true end
                end
                if lado == "R" and cerca(p.v, RES) and b > barras_rango + 20 then res_viva = true end
                if lado == "S" and cerca(p.v, RES) and b > barras_rango then giro = true end
            end
        end
    end

    comprobar("marca la resistencia 1.1050", res_marcada)
    comprobar("marca el soporte 1.1000", sop_marcado)
    comprobar("la resistencia repetida llega a ROJO (muy fuerte)", res_roja)
    comprobar("el soporte repetido llega a ROJO (muy fuerte)", sop_rojo)
    comprobar("la resistencia rota deja de dibujarse", not res_viva)
    comprobar("la resistencia rota reaparece como soporte", giro)
    comprobar("sin saltos en diagonal entre niveles", saltos_sin_hueco(engine, #rango) == 0)

    -- La línea tiene que pasar por los tres colores conforme gana toques
    local vistos = {}
    for nombre, serie in pairs(engine.plots) do
        if nombre:sub(1, 1) == "S" then
            for b = 1, #rango do
                local p = serie[b]
                if p and math.abs(p.v - SOP) < 0.0015 then vistos[p.c] = true end
            end
        end
    end
    comprobar("el soporte pasa por amarillo → verde → rojo",
              vistos[AMARILLO] and vistos[VERDE] and vistos[ROJO])
end

-- ---------------------------------------------------------------------
-- 2) Escenarios de robustez: no debe fallar nunca
-- ---------------------------------------------------------------------
print("")
print("2) Robustez")

local function escenario(nombre, candles, espera_lineas)
    local e = correr(candles)
    if not e then return end
    local usadas, puntos = lineas_usadas(e, #candles)
    local ok = espera_lineas and usadas > 0 or not espera_lineas
    comprobar(("%-22s velas=%4d lineas=%2d/12 puntos=%5d"):format(nombre, #candles, usadas, puntos), ok)
    comprobar(("%-22s sin saltos en diagonal"):format(nombre), saltos_sin_hueco(e, #candles) == 0)
end

math.randomseed(42)
local paseo, p1 = {}, 1.2000
for _ = 1, 800 do
    local o = p1
    local c = o + (math.random() - 0.5) * 0.0012
    paseo[#paseo + 1] = { o, math.max(o, c) + math.random() * 0.0004,
                             math.min(o, c) - math.random() * 0.0004, c }
    p1 = c
end
escenario("paseo aleatorio", paseo, true)

math.randomseed(11)
local tend, p2 = {}, 1.3000
for tramo = 1, 25 do
    local sube = (tramo % 3 ~= 0)                      -- 2 tramos arriba, 1 abajo
    for _ = 1, 6 + math.random(6) do
        local o = p2
        local c = o + (sube and 0.0007 or -0.0006) + (math.random() - 0.5) * 0.0004
        tend[#tend + 1] = { o, math.max(o, c) + math.random() * 0.0002,
                               math.min(o, c) - math.random() * 0.0002, c }
        p2 = c
    end
end
escenario("tendencia con retrocesos", tend, true)

math.randomseed(5)
local indice, p3 = {}, 35000.0
for _ = 1, 300 do
    local o = p3
    local c = o + (math.random() - 0.5) * 120
    indice[#indice + 1] = { o, math.max(o, c) + 30, math.min(o, c) - 30, c }
    p3 = c
end
escenario("índice de precio alto", indice, true)

local plano = {}
for _ = 1, 120 do plano[#plano + 1] = { 5.0, 5.0, 5.0, 5.0 } end
escenario("mercado plano (ATR 0)", plano, false)

local cortas = {}
for _ = 1, 4 do cortas[#cortas + 1] = { 1.0, 1.01, 0.99, 1.005 } end
escenario("solo 4 velas", cortas, false)

-- ---------------------------------------------------------------------
print("")
if fallos == 0 then
    print("TODO CORRECTO")
    os.exit(0)
else
    print(("FALLOS: %d"):format(fallos))
    os.exit(1)
end
