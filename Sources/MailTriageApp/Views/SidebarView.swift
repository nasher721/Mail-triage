import SwiftUI

struct SidebarView: View {
    @Binding var selection: SidebarDestination

    var body: some View {
        List(selection: $selection) {
            Section("Mail Triage") {
                ForEach(SidebarDestination.allCases) { destination in
                    Label(destination.title, systemImage: destination.systemImage)
                        .tag(destination)
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("Mail Triage")
    }
}
