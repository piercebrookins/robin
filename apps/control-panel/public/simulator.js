const params=new URLSearchParams(location.search),state=params.get("state")||"in_meeting";
const labels={joining:"Joining…",waiting_room:"Waiting room",in_meeting:"In meeting",sharing:"You are screen sharing"};
document.getElementById("sim-state").textContent=labels[state]||labels.in_meeting;
const events=state==="joining"?[["00:00","Opening Zoom","Connecting to meeting link"]]:state==="waiting_room"?[["00:00","Waiting room","The host will let you in soon"]]:[["00:00","Admitted","Meeting controls are available"],["00:04","Computer audio","Connected to recorded PCM fixture"]];
document.getElementById("sim-timeline").innerHTML=events.map(([time,title,detail])=>`<div class="event"><time>${time}</time><div><strong>${title}</strong><div>${detail}</div></div></div>`).join("");
if(state==="sharing"){document.getElementById("sim-share").textContent="Stop Share";document.getElementById("sim-state").classList.add("warning")}
