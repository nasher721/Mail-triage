import SwiftUI

struct ActivityView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        Group {
            if store.activity.isEmpty {
                ContentUnavailableView("No Activity", systemImage: "text.alignleft")
            } else {
                List(store.activity) { entry in
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: symbol(for: entry.kind))
                            .foregroundStyle(color(for: entry.kind))
                            .frame(width: 18)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(entry.message)
                                .textSelection(.enabled)
                            Text(entry.date, style: .time)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .navigationTitle("Activity")
    }

    private func symbol(for kind: ActivityEntry.Kind) -> String {
        switch kind {
        case .info: "info.circle"
        case .success: "checkmark.circle.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        }
    }

    private func color(for kind: ActivityEntry.Kind) -> Color {
        switch kind {
        case .info: .secondary
        case .success: .green
        case .warning: .orange
        case .error: .red
        }
    }
}
