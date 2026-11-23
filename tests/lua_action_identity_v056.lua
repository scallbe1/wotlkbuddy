-- Validate actual production legacy action identity audit with Lua 5.1.
local f=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local addon=f:read('*a'); f:close()
local snippet=assert(addon:match('%-%- BEGIN ACTION SPELL IDENTITY AUDIT\n(.-)\n%-%- END ACTION SPELL IDENTITY AUDIT'),
    'production audit helper not present')
local audit=assert(loadstring(snippet .. '\nreturn auditActionIdentity'))()

BOOKTYPE_SPELL='spell'
GetSpellInfo=function(id, bookType)
    if bookType=='spell' and id==9 then
        return 'Fireball','Rank 1','Icon_Fireball'
    elseif bookType=='spell' and id==10 then
        return 'Frost Armor','Rank 1','Icon_Frost_Armor'
    elseif id==133 then return 'Fireball','Rank 1','Icon_Fireball'
    elseif id==168 then return 'Frost Armor','Rank 1','Icon_Frost_Armor'
    elseif id==10 then return 'Blizzard','Rank 1','Icon_Blizzard'
    elseif id==1 then return 'Word of Recall (OLD)','Rank 1','Icon_Recall'
    end
end
GetSpellName=function(id,kind)
    if kind=='spell' and id==9 then return 'Fireball' end
    if kind=='spell' and id==10 then return 'Frost Armor' end
end
GetActionTexture=function(index)
    if index==2 or index==6 or index==7 then
        return 'Icon_Fireball'
    end
    if index==3 then return 'Icon_Frost_Armor' end
end
GetSpellLink=function(index, kind)
    if kind=='spell' and index==9 then
        return '|cff71d5ff|Hspell:133:0|h[Fireball]|h|r'
    end
    if kind=='spell' and index==10 then
        return '|cff71d5ff|Hspell:168:0|h[Frost Armor]|h|r'
    end
end
GetSpellBookItemInfo=nil

local function assertTuple(slot,raw,expectRaw,expectBook,expectID,expectDBIcon,expectBookIcon)
    local dbName,bookName,bookID,dbIcon,bookIcon=audit(slot,raw)
    assert(dbName==expectRaw and bookName==expectBook and
           bookID==expectID and dbIcon==expectDBIcon and
           bookIcon==expectBookIcon,
           string.format('slot=%s raw=%s got db=%s book=%s ID=%s dbIcon=%s bookIcon=%s',
             tostring(slot),tostring(raw),dbName,bookName,tostring(bookID),dbIcon,bookIcon))
end

for _,slot in ipairs({2,6,7}) do
    assertTuple(slot,9,'','Fireball',133,'-1','1')
end
assertTuple(3,10,'Blizzard','Frost Armor',168,'0','1')

-- In a standard implementation where GetActionInfo truly returns
-- a database ID, the database-ID candidate must match independently.
assertTuple(2,133,'Fireball','',0,'1','-1')

-- Some private clients omit GetSpellLink; a valid GetSpellBookItemInfo
-- must still supply a separately verified spellbook ID.
GetSpellLink=nil
GetSpellBookItemInfo=function(index,kind)
    if kind=='spell' and index==9 then return 'SPELL',133 end
end
assertTuple(2,9,'','Fireball',133,'-1','1')

-- Incomplete API observations must not fabricate a spell name/ID or
-- claim an icon match.
GetSpellBookItemInfo=nil
GetSpellInfo=nil
GetSpellName=nil
GetActionTexture=nil
local dbName,bookName,bookID,dbIcon,bookIcon=audit(2,9)
assert(dbName=='' and bookName=='' and bookID==0 and
       dbIcon=='-1' and bookIcon=='-1')
print('Read-only 3.3.5a action identity DB vs spellbook slot audit passed')
