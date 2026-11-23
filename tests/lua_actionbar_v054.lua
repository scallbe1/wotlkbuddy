-- Exercise the PRODUCTION 3.3.5a actionbar reporting code with Lua 5.1.
local file = assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local addon = file:read('*a')
file:close()
local helper = assert(addon:match('%-%- BEGIN ACTIONBAR BINDING HELPER\n(.-)\n%-%- END ACTIONBAR BINDING HELPER'))
local scan = assert(addon:match('%-%- BEGIN ACTIONBAR OBSERVATIONS\n(.-)\n    %-%- BEGIN RAW ACTIONBAR AUDIT'))
local audit = assert(addon:match('%-%- BEGIN ACTION SPELL IDENTITY AUDIT\n(.-)\n%-%- END ACTION SPELL IDENTITY AUDIT'))
local resolver = assert(addon:match('%-%- BEGIN VERIFIED ACTION SPELL RESOLVER\n(.-)\n%-%- END VERIFIED ACTION SPELL RESOLVER'))
local load = assert(loadstring(helper .. '\n' .. audit .. '\n' .. resolver ..
                         '\nlocal rows={}\nlocal function appendLine(row) table.insert(rows,row) end\n' ..
                         scan .. '\nreturn rows'))

GetActionInfo = function(slot)
    if slot == 2 or slot == 6 then return 'spell',133 end
    if slot == 4 then return 'macro',11 end
    return nil
end
GetSpellInfo = function(id)
    if id == 133 then return 'Fireball', 'Rank 1', 'Icon_Fireball' end
end
GetActionBarPage = function() return 1 end
GetActionTexture = function(slot)
    if slot == 2 or slot == 6 then return 'Icon_Fireball' end
end
GetBindingAction = function(key)
    if key == '2' then return 'ACTIONBUTTON2' end
    if key == '9' then return 'ACTIONBUTTON9' end
end
GetBindingKey = function(action)
    if action == 'ACTIONBUTTON6' then return 'SHIFT-6','6' end
end
IsUsableAction = function() return true end
IsActionInRange = function() return 1 end
GetActionCooldown = function() return 0,0,1 end
GetTime = function() return 10 end
function safe(value,limit)
    return string.sub(tostring(value),1,limit)
end

local function has(rows,expected)
    for _,row in ipairs(rows) do
        if row == expected then return true end
    end
    return false
end

local rows = load()
assert(has(rows,'AH\t1'),'Actionbar inventory must be acknowledged')
assert(has(rows,'AB\t2\t133\tFireball\t1'),'Existing Fireball slot 2 missing')
assert(has(rows,'AB\t6\t133\tFireball\t1'),'Existing Fireball slot 6 missing')
assert(has(rows,'AS\t2\t133\tFireball\t1\t1\t0'),'Direct digit 2 missing')
assert(has(rows,'AS\t6\t133\tFireball\t1\t1\t0'),'Second WoW binding key not recognized')
assert(has(rows,'ES\t9'),'Empty digit-bound slot 9 missing')
assert(not has(rows,'ES\t2'),'Occupied Fireball slot must never be available')

GetBindingKey = function() return 'SHIFT-6' end
rows = load()
assert(has(rows,'AB\t6\t133\tFireball\t0'),
    'Fireball slot 6 must be observed even with NO direct digit binding')
assert(not has(rows,'AS\t6\t133\tFireball\t1\t1\t0'),
    'Unbound slot must not authorize digit 6 to cast')

GetActionInfo = nil
rows = load()
assert(has(rows,'AH\t0'),'Unavailable action API must be explicit')
assert(#rows==1,'Unavailable action API must not report guessed slots')

print('WoW 3.3.5a actionbar duplicate / physical slot observations passed')
