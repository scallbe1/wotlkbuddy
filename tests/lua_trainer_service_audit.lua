local f=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local source=f:read('*a'); f:close()
local block=assert(string.match(source,'%-%- BEGIN TRAINER SERVICE AUDIT(.-)%-%- END TRAINER SERVICE AUDIT'))
local factory=assert(loadstring("local function safe(v,n) return string.sub(v or '',1,n) end\n"..block.."\nreturn trainerServiceRows"))
local read=factory()
local rows={{'Frost','', 'header',1},{'Frostbolt','Rank 1','available'},
            {'Frost Nova','Rank 1','available'},{'Blizzard','Rank 1','unavailable'},
            {'Fireball','Rank 1','used'}}
GetNumTrainerServices=function() return #rows end
GetTrainerServiceInfo=function(i) return unpack(rows[i]) end
GetTrainerServiceCost=function(i) return i==2 and 100 or 400 end
GetTrainerServiceLevelReq=function(i) return i==2 and 4 or 10 end
GetTrainerServiceTypeFilter=function() return 1 end
local services,audit=read()
assert(#services==2 and services[1]=='Y\t2\tFrostbolt\tRank 1\t100\t4')
assert(audit[1]==5 and audit[2]==2 and audit[3]==1 and audit[4]==1 and audit[9]==1)
rows[1][4]=nil
services,audit=read(); assert(audit[6]==1 and audit[9]==0)
rows[1][4]=1
GetTrainerServiceCost=function() return nil end
services,audit=read(); assert(#services==0 and audit[2]==2 and audit[9]==0)
GetTrainerServiceCost=function() return 100 end
GetTrainerServiceTypeFilter=function() return 0 end
services,audit=read(); assert(audit[8]==0 and audit[9]==0)
print('Trainer service audit: passed')
