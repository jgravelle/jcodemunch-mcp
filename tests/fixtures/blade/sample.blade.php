@extends('layouts.app')

@section('title', 'User Profile')

@section('content')
    <div class="container">
        <h1>{{ $user->name }}</h1>

        @include('partials.avatar', ['user' => $user])

        @component('alert', ['type' => 'success'])
            @slot('title')
                Welcome back!
            @endslot
            Your profile has been updated.
        @endcomponent

        @push('scripts')
            <script>console.log('profile loaded');</script>
        @endpush
    </div>
@endsection

@section('sidebar')
    @include('partials.nav')
    @livewire('user-stats', ['userId' => $user->id])
@endsection

@stack('scripts')

@yield('extra')
