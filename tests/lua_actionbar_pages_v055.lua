-- Actual production read-only actionbar page diagnostic, Lua 5.1.
local f=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local source=f:read('*a')
f:close()
local helper=assert(source:match('%-%- BEGIN ACTIONBAR BINDING HELPER\n(.-)\n%-%- END ACTIONBAR BINDING HELPER'))
local audit=assert(source:match('%-%- BEGIN RAW ACTIONBAR AUDIT\n(.-)\n    %-%- END RAW ACTIONBAR AUDIT'))
local identity=assert(source:match('%-%- BEGIN ACTION SPELL IDENTITY AUDIT\n(.-)\n%-%- END ACTION SPELL IDENTITY AUDIT'))
local resolver=assert(source:match('%-%- BEGIN VERIFIED ACTION SPELL RESOLVER\n(.-)\n%-%- END VERIFIED ACTION SPELL RESOLVER'))
local safe=function(value,limit)
    return string.sub(tostring(value),1,limit)
end
local factory=assert(loadstring(helper .. '\n' .. identity .. '\n' ..
    resolver .. '\nreturn function(appendLine,safe)\n' .. audit .. '\nend'))
local perform=factory()
local rows={}
local function append(row) table.insert(rows,row) return true end
GetActionBarPage=function() return 2 end
GetBindingAction=function(key) return 'ACTIONBUTTON' .. key end
GetBindingKey=nil
GetSpellInfo=function(id)
    if id==133 then return 'Fireball','Rank 1','Icon_Fireball' end
end
GetActionTexture=function(index)
    if index==14 or index==18 or index==19 then return 'Icon_Fireball' end
end
GetActionText=function() return nil end
GetActionInfo=function(index)
    if index==14 or index==18 or index==19 then return 'spell',133 end
    return nil
end
perform(append,safe)
local function contains(fragment)
    for _,row in ipairs(rows) do
        if row==fragment then return true end
    end
    return false
end
assert(contains('APG\t2'),'Client must announce page 2')
assert(contains('AR\tbase\t2\t2\tempty\t0\t\t1'),
       'Base slot 2 must be independently reported empty')
assert(contains('AR\tvisible\t2\t14\tspell\t133\tFireball\t1'),
       'Visible bar 2 must map to action index 14')
assert(contains('AR\tvisible\t6\t18\tspell\t133\tFireball\t1'),
       'Visible bar 6 must map to action index 18')
assert(contains('AR\tvisible\t7\t19\tspell\t133\tFireball\t1'),
       'Visible bar 7 must map to action index 19')
assert(#rows==21,'Page-2 audit must report page plus exactly 20 raw slots')
print('WoW actionbar paging raw inspection passed')
