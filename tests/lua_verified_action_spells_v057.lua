-- End-to-end Lua 5.1 regression for production actionbar + Fireball key.
-- Reproduces Aarr's actual WoW 3.3.5a observations: GetActionInfo
-- returns a spellBOOK INDEX, *not* the database spell ID.
local f=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local src=f:read('*a'); f:close()
local function chunk(open,close)
    return assert(src:match(open .. '\n(.-)\n' .. close))
end
local binding=chunk('%-%- BEGIN ACTIONBAR BINDING HELPER',
                    '%-%- END ACTIONBAR BINDING HELPER')
local audit=chunk('%-%- BEGIN ACTION SPELL IDENTITY AUDIT',
                  '%-%- END ACTION SPELL IDENTITY AUDIT')
local resolver=chunk('%-%- BEGIN VERIFIED ACTION SPELL RESOLVER',
                     '%-%- END VERIFIED ACTION SPELL RESOLVER')
local bar=chunk('    %-%- BEGIN ACTIONBAR OBSERVATIONS',
                '    %-%- BEGIN RAW ACTIONBAR AUDIT')
local fire=assert(src:match("    (local fireballKey = '%-'.-)\n    local remaining = %-1"))
local factory=assert(loadstring(binding .. '\n' .. audit .. '\n' .. resolver ..
    '\nreturn function(appendLine,safe)\n' .. bar .. '\n' .. fire ..
    '\nreturn fireballKey\nend'))
local scan=factory()
local function safe(value,max) return string.sub(tostring(value or ''),1,max) end
local books={
 [1]={'Auto Attack',6603,'Icon_Attack'},
 [2]={'Cannibalize',20577,'Icon_Cannibalize'},
 [8]={'Arcane Intellect',1459,'Icon_ArcaneIntellect'},
 [9]={'Fireball',133,'Icon_Fireball'},
 [10]={'Frost Armor',168,'Icon_FrostArmor'}
}
local db={
 [1]={'Word of Recall (OLD)','Icon_Recall'},
 [10]={'Blizzard','Icon_Blizzard'},
 [133]={'Fireball','Icon_Fireball'},
 [168]={'Frost Armor','Icon_FrostArmor'},
 [6603]={'Auto Attack','Icon_Attack'},
 [20577]={'Cannibalize','Icon_Cannibalize'},
 [1459]={'Arcane Intellect','Icon_ArcaneIntellect'}
}
local physical={[1]=1,[2]=9,[3]=10,[4]=2,[5]=8,[6]=9,[7]=9}
BOOKTYPE_SPELL='spell'
GetActionBarPage=function() return 1 end
GetActionInfo=function(slot)
    return physical[slot] and 'spell' or nil, physical[slot]
end
GetBindingAction=function(key)
    if tonumber(key) then return 'ACTIONBUTTON' .. key end
end
GetBindingKey=nil
GetSpellInfo=function(id,kind)
    local row = kind=='spell' and books[id] or db[id]
    if row then
        if kind=='spell' then return row[1],'Rank 1',row[3] end
        return row[1],'Rank 1',row[2]
    end
end
GetSpellName=function(index,kind)
    return kind=='spell' and books[index] and books[index][1]
end
GetSpellLink=function(index,kind)
    if kind=='spell' and books[index] then
        local id,name=books[index][2],books[index][1]
        return '|cff71d5ff|Hspell:' .. id .. ':0|h[' .. name .. ']|h|r'
    end
end
GetSpellBookItemInfo=nil
GetActionTexture=function(slot)
    local book=books[physical[slot]]
    if book then return book[3] end
    -- A stock client returns a database spell ID from GetActionInfo.
    -- Its action icon still comes from the same visible button.
    local database=db[physical[slot]]
    return database and database[2]
end
IsUsableAction=function() return true end
IsActionInRange=function() return 1 end
GetActionCooldown=function() return 0,0,1 end
GetTime=function() return 10 end

local function snapshot()
    local rows={}
    local key=scan(function(row) table.insert(rows,row) end,safe)
    local found={}
    for _,row in ipairs(rows) do found[row]=true end
    return found,key
end
local rows,key=snapshot()
assert(key=='2','Fireball F hotkey must resolve to actual button 2')
local expected={
 'AB\t1\t6603\tAuto Attack\t1',
 'AB\t2\t133\tFireball\t1',
 'AB\t3\t168\tFrost Armor\t1',
 'AB\t4\t20577\tCannibalize\t1',
 'AB\t5\t1459\tArcane Intellect\t1',
 'AB\t6\t133\tFireball\t1',
 'AB\t7\t133\tFireball\t1',
 'AS\t2\t133\tFireball\t1\t1\t0',
 'AS\t3\t168\tFrost Armor\t1\t1\t0',
 'AS\t6\t133\tFireball\t1\t1\t0',
 'AS\t7\t133\tFireball\t1\t1\t0',
 'ES\t8'}
for _,entry in ipairs(expected) do
    assert(rows[entry], 'Expected resolved actionbar record missing: ' .. entry)
end
assert(not rows['AB\t3\t10\tBlizzard\t1'],
       'The raw book index 10 must NEVER be misinterpreted as Blizzard')
assert(not rows['AB\t1\t1\tWord of Recall (OLD)\t1'],
       'The raw book index 1 must NEVER be misinterpreted as Word of Recall')

-- A standard client can still return a real database spell ID.
physical[2]=133
rows,key=snapshot()
assert(rows['AS\t2\t133\tFireball\t1\t1\t0'],
       'Actual database ID 133 should continue to work when icon matches')
assert(key=='2')

-- Icon failure must withhold both AB and AS for that slot, not guess.
physical[2]=9
local originalTexture=GetActionTexture
GetActionTexture=function(slot)
    if slot==2 then return 'Wrong_Icon' end
    return originalTexture(slot)
end
rows,key=snapshot()
assert(not rows['AB\t2\t133\tFireball\t1'] and
       not rows['AS\t2\t133\tFireball\t1\t1\t0'],
       'Wrong icon must block action spell verification')
assert(key=='6','Other independently verified Fireball button should work')

-- An ambiguous shared icon cannot pick between two different spell IDs.
GetActionTexture=originalTexture
db[10][2]='Icon_FrostArmor' -- deliberately wrong Blizzard icon
rows,key=snapshot()
assert(not rows['AB\t3\t168\tFrost Armor\t1'] and
       not rows['AS\t3\t168\tFrost Armor\t1\t1\t0'],
       'Conflicting icon-matched spell IDs must be rejected')
assert(key=='2')

-- Missing live spellbook link + no book-item ID must withhold all
-- book-index-cast permissions, rather than typing a /cast workaround.
GetSpellLink=nil
db[10][2]='Icon_Blizzard'
rows,key=snapshot()
assert(key=='-' and not rows['AS\t2\t133\tFireball\t1\t1\t0'],
       'No verified link/ID must fail closed')
print('Verified raw spellbook-index -> real spell ID/action key regression passed')
