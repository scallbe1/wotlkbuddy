local f=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local src=f:read('*a'); f:close()
local code=assert(src:match('%-%- BEGIN VERIFIED EQUIPMENT REPAIR\n(.-)%-%- END VERIFIED EQUIPMENT REPAIR'))
local price,cash,combat,dead,capable,allowed=25,250,false,false,true,true
local guid='0xF130000DC2000001'
local repaired=0
SlashCmdList={}
MerchantFrame={selectedTab=1,IsShown=function() return true end}
UnitAffectingCombat=function() return combat end
UnitIsDeadOrGhost=function() return dead end
UnitGUID=function() return guid end
GetMoney=function() return cash end
CanMerchantRepair=function() return capable end
GetRepairAllCost=function() return price,allowed end
RepairAllItems=function() repaired=repaired+1 end
assert(loadstring(code))()
local run=SlashCmdList.WOTLKBUDDYREPAIR
run('3522 250')
assert(repaired==1)
run('3523 250')
run('3522 24')
run('3522 1000001')
run('3522 0')
cash=24; run('3522 250'); cash=250
combat=true; run('3522 250'); combat=false
dead=true; run('3522 250'); dead=false
capable=false; run('3522 250'); capable=true
allowed=false; run('3522 250'); allowed=true
price=0; run('3522 250'); price=25
MerchantFrame.selectedTab=2; run('3522 250')
assert(repaired==1,'Unsafe repair was authorized')
print('Verified repair transaction guards passed')
