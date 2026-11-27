-- Run the production merchant candidate encoder under Lua 5.1.
local f=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local src=f:read('*a'); f:close()
local block=assert(src:match('%-%- BEGIN MERCHANT CANDIDATE BATCH\n(.-)    %-%- END MERCHANT CANDIDATE BATCH'))
local scan=assert(loadstring('return function(CAPACITY, payloadBytes)\n' ..
    [[
    local weaponSkills={}
    local lines={}
    local function safe(value, n) return string.sub(tostring(value or ''),1,n) end
    local function appendLine(line)
        payloadBytes=payloadBytes+#line+1
        assert(payloadBytes<=CAPACITY-20,'merchant batch exceeded capacity')
        lines[#lines+1]=line
    end
    ]] .. block .. '\nreturn lines\nend'))()

local visible=true
MerchantFrame={IsShown=function() return visible end,selectedTab=1}
GetContainerNumSlots=function(bag) return bag==0 and 5 or 0 end
GetContainerItemLink=function(bag,slot) return 'item:'..(1000+slot) end
GetContainerItemInfo=function(bag,slot) return 'texture',1,slot==5 end
GetContainerItemQuestInfo=function(bag,slot) return slot==4 end
GetItemInfo=function(link)
    if link=='item:1001' then return nil end -- unresolved slot
    return 'known',link,0,1,0,'Miscellaneous','Junk',1,'','texture',3
end

local rows=scan(2048,0)
assert(rows[1]=='M\t1\t2\t1',table.concat(rows,'|'))
assert(rows[2]:match('^D\t0\t2\t1002\t'))
assert(rows[3]:match('^D\t0\t3\t1003\t'))
assert(#rows==3,'quest, locked and unresolved slots must remain excluded')

-- Space for one candidate, not both. Same stable first slot, no truncation.
local limited=scan(20+12+#rows[2]+1,0)
assert(limited[1]=='M\t1\t1\t1')
assert(limited[2]==rows[2] and #limited==2)

-- Never authorize a live batch if even its first candidate cannot fit.
assert(scan(32,0)[1]=='M\t0\t0\t1')

-- Off-merchant resale estimates must remain all-or-nothing.
visible=false
assert(scan(2048,0)[1]=='M\t0\t0\t0')
visible=true
MerchantFrame.selectedTab=2
assert(scan(2048,0)[1]=='M\t0\t0\t1','buyback tab must remain blocked')
MerchantFrame.selectedTab=1
GetContainerItemQuestInfo=nil
assert(scan(2048,0)[1]=='M\t0\t0\t1','missing quest API must remain blocked')
print('Merchant candidate batch regressions passed')
