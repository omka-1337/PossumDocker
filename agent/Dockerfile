# The agent: one static binary, nothing else in the image.

FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /agent ./cmd/agent

FROM scratch
COPY --from=build /agent /agent
EXPOSE 8081
# No shell in the image: the binary checks itself.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD ["/agent", "healthcheck"]
ENTRYPOINT ["/agent"]
