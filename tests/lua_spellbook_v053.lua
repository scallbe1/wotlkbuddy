-- Actual Lua 5.1 resolver test using the helper extracted from the addon.
-- Run with lua5.1 tests/lua_spellbook_v053.lua (no WoW client needed).
local f = assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua', 'r'))
local addon = f:read('*a')
f:close()
local source = assert(addon:match('%-%- BEGIN SPELLBOOK PROBE HELPER\n(.-)\n%-%- END SPELLBOOK PROBE HELPER'),
    'addon spellbook helper missing')
local makeResolver = assert(loadstring(source .. '\nreturn resolveSpellbook'))
local resolve = makeResolver()

BOOKTYPE_SPELL = 'spell'
local names = {[1]='Attack', [2]='Fireball', [3]='Frostbolt', [4]='Fireball'}
local ids = {[1]=6603, [2]=133, [3]=116, [4]=143}
local spellNames = {[133]='Fireball', [143]='Fireball', [116]='Frostbolt', [6603]='Attack'}
GetSpellName = function(index,bookType)
    assert(bookType == 'spell')
    return names[index]
end
GetSpellInfo = function(id, bookType)
    if bookType == 'spell' then return names[id] end
    return spellNames[id]
end
GetNumSpellTabs = function() return 1 end
GetSpellTabInfo = function(tab)
    assert(tab == 1)
    return 'General','',0,4
end
GetSpellBookItemInfo = nil
GetSpellLink = function(index,bookType)
    return '|cff71d5ff|Hspell:' .. ids[index] .. ':0|h[' .. names[index] .. ']|h|r'
end

local slot,id,status,matches=resolve('Fireball')
assert(slot==4 and id==143 and status=='OK' and matches==2,
       'Legacy spell-link fallback must resolve highest observed spellbook rank')
slot,id,status,matches=resolve('Frostbolt')
assert(slot==3 and id==116 and status=='OK' and matches==1,
       'Legacy spell-link must work for another class ability')
slot,id,status,matches=resolve('Fire Blast')
assert(slot==0 and id==0 and status=='NO_MATCH' and matches==0,
       'Unlearned spell must not fabricate a spellbook record')

-- Current API available: exact spell item ID is preferred.
GetSpellBookItemInfo = function(index, bookType)
    return 'SPELL', ids[index]
end
GetSpellLink = nil
slot,id,status,matches=resolve('Fireball')
assert(slot==4 and id==143 and status=='OK' and matches==2,
       'GetSpellBookItemInfo path must continue to work')

-- API returns an ID that does not identify the requested spell. Fail closed.
GetSpellBookItemInfo = function(index, bookType)
    return 'SPELL', 99999
end
slot,id,status,matches=resolve('Fireball')
assert(slot==0 and id==0 and status=='NO_ID' and matches==2,
       'Invalid spell IDs must never be sent to PlaceAction')

-- Missing GetSpellBookItemInfo and missing GetSpellLink: diagnostic rather
-- than an incorrect claim that the character has not learned Fireball.
GetSpellBookItemInfo = nil
slot,id,status,matches=resolve('Fireball')
assert(status=='NO_ID' and matches==2,
       'Missing spell ID API must be diagnosed as NO_ID, not NO_MATCH')

-- Some custom clients have no valid spell-tab enumeration at all.
GetSpellLink = function(index,bookType) return nil, ids[index] end
GetNumSpellTabs = nil
GetSpellTabInfo = nil
slot,id,status,matches=resolve('Fireball')
assert(slot==4 and id==143 and status=='OK' and matches==2,
       'Bounded book-slot fallback must tolerate absent spell-tab functions')

-- Both name resolvers unavailable: no false positive.
GetSpellName = nil
GetSpellInfo = nil
slot,id,status,matches=resolve('Fireball')
assert(status=='NO_NAME_API' and slot==0 and id==0,
       'Missing spell-name API must be explicit')
print('Legacy WoW 3.3.5a spellbook probe Lua tests passed')
