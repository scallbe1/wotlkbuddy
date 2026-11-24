-- Exercise the exact production native HUD destination resolver and slash
-- handler using WoW's Lua 5.1 semantics, without loading the full addon UI.
local file=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local src=file:read('*a');file:close()
local code=assert(src:match('%-%- BEGIN HUD NAVIGATION CONTEXT\n(.-)\n%-%- END HUD NAVIGATION CONTEXT'))
local factory=assert(loadstring([[
return function()
    local hudTravelKind,hudTravelName='',''
    local hudQuestName='The Scarlet Crusade'
    local statusFrame={Show=function() end}
    SlashCmdList={}
]] .. code .. [[
    return function(questName)
        return travelActionLabel(hudTravelKind,hudTravelName,questName or hudQuestName)
    end, SlashCmdList['WOTLKBUDDYTRAVEL']
end
]]))
local label,command=factory()()
assert(type(command)=='function')
command('enemies')
assert(label()=='Navigating to The Scarlet Crusade enemies',
       'Travel must name active quest enemies, including during scan stops')
command('npc Executor Arren')
assert(label()=='Navigating to Executor Arren')
command('npc Isabella')
assert(label()=='Navigating to Isabella')
command('corpse')
assert(label()=='Navigating to quest corpse')
command('firing')
assert(label()=='Navigating to firing position')
command('npc Executor Arren;print(1)')
assert(label()=='Navigating to firing position',
       'Invalid injected NPC text must not change native HUD')
command('clear')
assert(label()==nil,'Arrival or phase exit must clear travel state')
command('enemies')
assert(label('Night Web\'s Hollow')==
       "Navigating to Night Web's Hollow enemies")
print('Native WoW navigation destination action HUD semantics passed')
